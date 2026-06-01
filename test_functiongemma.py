import requests
import json

BASE_URL = "https://functiongemma-classifier-373347358125.us-central1.run.app"

def test_health():
    print("--- Testing /health ---")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status Code: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    print()

def test_intent_routing():
    print("--- Testing Intent Routing Classification ---")
    schema = {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["bug_report", "feature_request", "general_inquiry", "how_to"]},
            "confidence": {"type": "number"},
            "urgency": {"type": "string", "enum": ["high", "medium", "low"]}
        },
        "required": ["intent", "confidence", "urgency"]
    }
    
    response = requests.post(
        f"{BASE_URL}/generate",
        json={
            "messages": [
                {"role": "system", "content": "You are an intelligent support ticket classifier."},
                {"role": "user", "content": "The checkout page crashes with a 500 error every time I try to pay with Apple Pay. Please fix this immediately, we are losing sales!"}
            ],
            "json_schema": schema,
            "temperature": 0.1,
            "max_tokens": 150
        }
    )
    
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        print(json.dumps(response.json(), indent=2))
    else:
        print(response.text)
    print()

def test_web_search_decomposition():
    print("--- Testing WebRAG Query Decomposition ---")
    schema = {
        "type": "object",
        "properties": {
            "should_decompose": {"type": "boolean"},
            "sub_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "target_provider": {"type": "string", "enum": ["keyword", "community", "neural"]}
                    },
                    "required": ["question", "target_provider"]
                }
            }
        },
        "required": ["should_decompose", "sub_questions"]
    }
    
    response = requests.post(
        f"{BASE_URL}/generate",
        json={
            "messages": [
                {"role": "system", "content": "You are a Web Search decomposition agent. Break complex queries into targeted sub-queries."},
                {"role": "user", "content": "Compare the performance of React 19 vs Vue 4, and tell me what developers on Reddit think about the developer experience."}
            ],
            "json_schema": schema,
            "temperature": 0.1,
            "max_tokens": 300
        }
    )
    
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        print(json.dumps(response.json(), indent=2))
    else:
        print(response.text)
    print()

if __name__ == "__main__":
    test_health()
    test_intent_routing()
    test_web_search_decomposition()
