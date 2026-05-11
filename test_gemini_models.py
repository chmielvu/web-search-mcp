import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
import asyncio
import sys
import os
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')

print('=== ENV CHECK ===')
gemini_key = os.environ.get('KINDLY_GEMINI_API_KEY', 'NOT SET')
print(f'KINDLY_GEMINI_API_KEY: {gemini_key[:20]}...' if gemini_key != 'NOT SET' else 'NOT SET')
print(f'KINDLY_GEMINI_GROUNDING_MODEL: {os.environ.get("KINDLY_GEMINI_GROUNDING_MODEL", "NOT SET")}')

from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ['KINDLY_GEMINI_API_KEY'])

async def test_model(model_id, query):
    print(f'\n=== TESTING MODEL: {model_id} ===')
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.1,
    )
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model_id,
            contents=query,
            config=config,
        )
        print(f'Status: SUCCESS')
        print(f'Candidates: {len(response.candidates or [])}')
        if response.candidates:
            c = response.candidates[0]
            print(f'Content parts: {len(c.content.parts or [])}')
            gm = getattr(c, 'grounding_metadata', None)
            if gm:
                chunks = getattr(gm, 'grounding_chunks', None) or []
                print(f'Grounding chunks: {len(chunks)}')
                for i, ch in enumerate(chunks[:5]):
                    web = getattr(ch, 'web', None)
                    if web:
                        title = getattr(web, 'title', '?')
                        uri = getattr(web, 'uri', '?')
                        print(f'  [{i}] {title} -> {uri}')
            else:
                print('NO grounding_metadata')
        return True
    except Exception as e:
        print(f'Status: FAILED')
        print(f'Error type: {type(e).__name__}')
        print(f'Error: {e}')
        return False

async def main():
    models_to_test = [
        'gemma-4-31b-it',
        'gemini-2.5-flash',
        'gemini-2.0-flash',
    ]
    for m in models_to_test:
        await test_model(m, 'What is Claude AI?')
        await asyncio.sleep(0.5)

asyncio.run(main())