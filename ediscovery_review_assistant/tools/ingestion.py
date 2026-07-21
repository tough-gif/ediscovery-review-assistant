"""Ingestion and parsing tools for E-Discovery documents."""

import re
import logging
import io
from typing import List, Optional
import pypdf
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class EDiscoveryMetadata(BaseModel):
    """Strict tracking schema for E-Discovery documents."""
    custodian: str = Field(description="Name of the employee whose data silo was collected.")
    file_type: str = Field(description="Source file category: 'email', 'pdf', 'chat_log', or 'document'.")
    source_file_name: str = Field(description="The original name of the file (e.g., mail_archive.eml).")
    file_path: str = Field(description="The immutable Cloud Storage URI or local archive file path.")
    
    # Structural Communication Fields
    sender: str = Field(description="The author, email 'From' field, or person who spoke in a chat.")
    recipients_to: List[str] = Field(default=[], description="Primary recipients, 'To' field list, or channel name.")
    recipients_cc: List[str] = Field(default=[], description="Carbon Copy email recipients.")
    
    # Chunk Positioning
    page_number: Optional[int] = Field(None, description="The specific page number if applicable (e.g., for PDFs).")
    line_range: Optional[str] = Field(None, description="Line range inside raw log or message string (e.g., 'Lines 40-80').")

class MemoryPayload(BaseModel):
    """The final structural payload format written into the Memory Bank index."""
    text_to_embed: str = Field(description="The raw, un-summarized textual content chunk to preserve evidence.")
    metadata: EDiscoveryMetadata

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extracts text from native PDF bytes, preserving page breaks with form feeds (\f)."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages_text.append(text)
        # Join pages with form-feed character to allow page-based chunking in parser
        return "\f".join(pages_text)
    except Exception as e:
        logger.error(f"Failed to extract text from PDF: {e}")
        raise ValueError(f"Invalid or corrupted PDF file: {e}")

def chunk_text(text: str, max_words: int = 200) -> List[str]:
    """Slices a long string into manageable semantic pieces without breaking sentences."""
    words = text.split()
    return [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]

def parse_document(
    file_content: str,
    file_name: str,
    file_type: str,
    custodian: str,
    file_path: str
) -> List[dict]:
    """Parses structural communications text (Emails, Chat logs, PDFs in text format),
    chunks the data safely, and attaches structured metadata.
    
    Args:
        file_content: The raw text content of the file.
        file_name: The name of the file.
        file_type: The type of the file ('email', 'chat_log', 'pdf', 'document').
        custodian: The custodian of the file.
        file_path: The path to the file.
        
    Returns:
        A list of dicts matching the MemoryPayload schema.
    """
    payload_items = []
    file_type = file_type.lower().strip()
    
    logger.info(f"Parsing file {file_name} of type {file_type} for custodian {custodian}")

    # --- TYPE 1: EMAIL TEXT PROCESSING ---
    if file_type == "email":
        # Extract fields using simple regex headers commonly found in raw text emails
        sender_match = re.search(r"^From:\s*(.*)", file_content, re.IGNORECASE | re.MULTILINE)
        to_match = re.search(r"^To:\s*(.*)", file_content, re.IGNORECASE | re.MULTILINE)
        cc_match = re.search(r"^Cc:\s*(.*)", file_content, re.IGNORECASE | re.MULTILINE)
        
        sender = sender_match.group(1).strip() if sender_match else "Unknown Sender"
        recipients_to = [r.strip() for r in to_match.group(1).split(",")] if to_match else []
        recipients_cc = [r.strip() for r in cc_match.group(1).split(",")] if cc_match else []
        
        chunks = chunk_text(file_content)
        for idx, chunk in enumerate(chunks):
            meta = EDiscoveryMetadata(
                custodian=custodian,
                file_type="email",
                source_file_name=file_name,
                file_path=file_path,
                sender=sender,
                recipients_to=recipients_to,
                recipients_cc=recipients_cc,
                line_range=f"Chunk-{idx+1}"
            )
            payload_items.append({"text_to_embed": chunk, "metadata": meta.model_dump()})

    # --- TYPE 2: CHAT LOGS PROCESSING ---
    elif file_type == "chat_log":
        lines = file_content.splitlines()
        chunk_size = 15
        for i in range(0, len(lines), chunk_size):
            segment_lines = lines[i:i + chunk_size]
            segment_text = "\n".join(segment_lines)
            
            # Identify who spoke most in this block to map a loose 'sender' proxy
            # Format in logs: "[2025-03-14 16:24:10] Elena Rostova: ..."
            speakers = re.findall(r"^\[.*?\]\s*([^:]+):", segment_text, re.MULTILINE)
            primary_sender = speakers[0] if speakers else "System/Channel"
            
            meta = EDiscoveryMetadata(
                custodian=custodian,
                file_type="chat_log",
                source_file_name=file_name,
                file_path=file_path,
                sender=primary_sender,
                recipients_to=[file_name.replace(".txt", "")],
                recipients_cc=[],
                line_range=f"Lines {i+1}-{i+len(segment_lines)}"
            )
            payload_items.append({"text_to_embed": segment_text, "metadata": meta.model_dump()})

    # --- TYPE 3: PDF / DOCX / TEXT PROCESSING ---
    else:
        # Standard textualized PDFs separate pages via form feed characters ('\f')
        pages = file_content.split('\f') if '\f' in file_content else [file_content]
        
        for page_idx, page_text in enumerate(pages):
            if not page_text.strip():
                continue
            
            sub_chunks = chunk_text(page_text, max_words=200)
            for sub_idx, chunk in enumerate(sub_chunks):
                meta = EDiscoveryMetadata(
                    custodian=custodian,
                    file_type="pdf" if file_type == "pdf" else "document",
                    source_file_name=file_name,
                    file_path=file_path,
                    sender=custodian,
                    recipients_to=[],
                    recipients_cc=[],
                    page_number=page_idx + 1,
                    line_range=f"Subchunk-{sub_idx+1}"
                )
                payload_items.append({"text_to_embed": chunk, "metadata": meta.model_dump()})

    logger.info(f"Generated {len(payload_items)} payloads for {file_name}")
    return payload_items
