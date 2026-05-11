"""Test Gemini grounding with working models."""
import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
import asyncio
import sys
import os
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')

from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ['KINDLY_GEMINI_API_KEY'])

async def test_gemini_grounding(model_id, query):
    """Test Gemini grounding and inspect full response."""
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
        print(f'HTTP Status: 200')

        if response.candidates:
            c = response.candidates[0]
            print(f'\n--- CONTENT ---')
            parts = getattr(getattr(c, 'content', None), 'parts', None) or []
            for i, part in enumerate(parts):
                if hasattr(part, 'text') and part.text:
                    is_thought = getattr(part, 'thought', False)
                    print(f'Part[{i}] (thought={is_thought}): {part.text[:200]}...')

            print(f'\n--- GROUNDING METADATA ---')
            gm = getattr(c, 'grounding_metadata', None)
            if gm:
                chunks = getattr(gm, 'grounding_chunks', None) or []
                print(f'Grounding chunks: {len(chunks)}')
                for i, ch in enumerate(chunks[:10]):
                    web = getattr(ch, 'web', None)
                    if web:
                        title = getattr(web, 'title', '?')
                        uri = getattr(web, 'uri', '?')
                        print(f'  [{i}] {title[:50]}... -> {uri}')

                queries = getattr(gm, 'web_search_queries', None) or []
                print(f'\nWeb search queries: {queries}')

                supports = getattr(gm, 'grounding_supports', None) or []
                print(f'Grounding supports: {len(supports)}')
            else:
                print('NO grounding_metadata')

        return True
    except Exception as e:
        print(f'Status: FAILED')
        print(f'Error type: {type(e).__name__}')
        print(f'Error: {e}')
        return False

async def main():
    # Test models known to support grounding
    models_to_test = [
        'gemini-2.5-flash',
        'gemini-2.0-flash',
        'gemini-1.5-flash',
    ]
    for m in models_to_test:
        await test_gemini_grounding(m, 'What is Claude AI?')
        await asyncio.sleep(1)

asyncio.run(main())