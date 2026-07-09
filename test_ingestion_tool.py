import os
from ediscovery_review_assistant.tools.ingestion import parse_document

def test_ingestion():
    sample_data_dir = "sample_data"
    
    # Define test cases: (filename, file_type, custodian, expected_privilege)
    test_cases = [
        ("doc1_hot_slack_logs.txt", "chat_log", "Elena Rostova", False),
        ("doc2_privileged_lease.eml", "email", "Sarah Jenkins", True),
        ("doc3_mixed_liability_chain.eml", "email", "David Vance", True),
        ("doc4_collusion_pricing.eml", "email", "David Vance", False),
        ("doc5_hr_noise_picnic.eml", "email", "HR", False),
    ]
    
    for filename, file_type, custodian, expected_privilege in test_cases:
        filepath = os.path.join(sample_data_dir, filename)
        with open(filepath, "r") as f:
            content = f.read()
            
        payloads = parse_document(
            file_content=content,
            file_name=filename,
            file_type=file_type,
            custodian=custodian,
            file_path=f"gs://test-bucket/{custodian}/{filename}"
        )
        
        print(f"\n--- Testing {filename} ---")
        print(f"Generated {len(payloads)} chunks.")
        
        # Verify all chunks have the expected privilege flag
        for idx, p in enumerate(payloads):
            meta = p["metadata"]
            print(f"  Chunk {idx+1}: Privilege Flag = {meta['heuristic_privilege_flag']}")
            assert meta["heuristic_privilege_flag"] == expected_privilege, \
                f"Expected privilege {expected_privilege} for {filename} chunk {idx+1}, but got {meta['heuristic_privilege_flag']}"
            
            # Verify other basic metadata
            assert meta["custodian"] == custodian
            assert meta["source_file_name"] == filename
            assert meta["file_type"] == ("chat_log" if file_type == "chat_log" else "email") # In our tool, email type maps to email, chat_log to chat_log
            
        print(f"✅ {filename} passed validation.")

if __name__ == "__main__":
    try:
        test_ingestion()
        print("\n🎉 All ingestion tool tests passed successfully!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
