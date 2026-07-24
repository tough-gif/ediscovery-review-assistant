"""Model Armor Guard helper for sanitizing prompts and responses."""

import logging
from google.cloud import modelarmor_v1
from google.api_core.client_options import ClientOptions
from ediscovery_review_assistant.config import config

logger = logging.getLogger(__name__)

class ModelArmorGuard:
    """Performs safety policy filtering on LLM inputs and outputs using Google Cloud Model Armor."""

    def __init__(self):
        self.template_id = config.model_armor_template_id
        self.project_id = config.model_armor_project_id
        self.location = config.model_armor_location
        
        if not self.template_id:
            logger.info("Model Armor is not configured (MODEL_ARMOR_TEMPLATE_ID is empty). Safety checks will be bypassed.")
            self.client = None
            self.template_name = ""
            return

        self.template_name = f"projects/{self.project_id}/locations/{self.location}/templates/{self.template_id}"
        endpoint = f"modelarmor.{self.location}.rep.googleapis.com"
        
        logger.info(f"Initializing Model Armor Client for endpoint: {endpoint} using template: {self.template_name}")
        self.client = modelarmor_v1.ModelArmorClient(
            client_options=ClientOptions(api_endpoint=endpoint)
        )

    def scan_prompt(self, prompt: str) -> tuple[bool, str]:
        """Scans the user prompt for jailbreaks, prompt injections, and PII.

        Returns:
            (is_blocked, sanitized_text_or_error_message)
        """
        if not self.client:
            return False, prompt

        try:
            user_prompt_data = modelarmor_v1.DataItem(text=prompt)
            request = modelarmor_v1.SanitizeUserPromptRequest(
                name=self.template_name,
                user_prompt_data=user_prompt_data
            )
            
            logger.info("Invoking Model Armor sanitize_user_prompt...")
            response = self.client.sanitize_user_prompt(request=request)
            result = response.sanitization_result
            
            if result.filter_match_state == modelarmor_v1.FilterMatchState.MATCH_FOUND:
                logger.warning(f"⚠️ User prompt was flagged by Model Armor filters: {result.filter_results}")
                return True, "Your query was blocked by corporate safety policies (potential injection/jailbreak/PII violation)."
            
            # If prompt was sanitized (e.g. masked PII) but not blocked, use the sanitized text
            sanitized_text = prompt
            if hasattr(result, "sanitized_user_prompt_data") and result.sanitized_user_prompt_data and result.sanitized_user_prompt_data.text:
                sanitized_text = result.sanitized_user_prompt_data.text
                if sanitized_text != prompt:
                    logger.info("PII masking applied to the user prompt.")
                    
            return False, sanitized_text

        except Exception as e:
            logger.error(f"Error during Model Armor prompt scan: {e}", exc_info=True)
            # Fail closed in production mode
            return True, f"Security service execution error: {e}"

    def scan_response(self, text: str) -> tuple[bool, str]:
        """Scans the model response output before presenting it to the user.

        Returns:
            (is_blocked, sanitized_text_or_error_message)
        """
        if not self.client:
            return False, text

        try:
            model_response_data = modelarmor_v1.DataItem(text=text)
            request = modelarmor_v1.SanitizeModelResponseRequest(
                name=self.template_name,
                model_response_data=model_response_data
            )
            
            logger.info("Invoking Model Armor sanitize_model_response...")
            response = self.client.sanitize_model_response(request=request)
            
            if response.filter_match_state == modelarmor_v1.FilterMatchState.MATCH_FOUND:
                logger.warning("⚠️ Model response was flagged by Model Armor filters.")
                return True, "Response blocked by safety policy. The generated answer contained sensitive or restricted info."
            
            # Use sanitized response (e.g. with masked PII) if modifications were made
            sanitized_text = text
            if hasattr(response, "sanitized_model_response_data") and response.sanitized_model_response_data and response.sanitized_model_response_data.text:
                sanitized_text = response.sanitized_model_response_data.text
                if sanitized_text != text:
                    logger.info("PII masking applied to the generated model response.")
                    
            return False, sanitized_text

        except Exception as e:
            logger.error(f"Error during Model Armor response scan: {e}", exc_info=True)
            return True, f"Security service execution error: {e}"
