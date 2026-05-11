"""Detailed diagnostic test for Gemini grounding and DDG."""
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
print(f'KINDLY_GEMINI_SEARCH_MODE: {os.environ.get("KINDLY_GEMINI_SEARCH_MODE", "NOT SET")}')

from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ['KINDLY_GEMINI_API_KEY'])

async def test_gemini_full_inspection(model_id, query):
    """Full inspection of Gemini response structure."""
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

        print(f'\n--- RAW RESPONSE STRUCTURE ---')
        print(f'response type: {type(response).__name__}')
        print(f'Has candidates: {hasattr(response, "candidates")}')
        print(f'Candidates count: {len(response.candidates or [])}')

        if response.candidates:
            c = response.candidates[0]
            print(f'\nCandidate[0] attributes: {dir(c)}')
            print(f'Has content: {hasattr(c, "content")}')
            print(f'Has grounding_metadata: {hasattr(c, "grounding_metadata")}')

            # Content inspection
            content = getattr(c, 'content', None)
            if content:
                print(f'\nContent attributes: {dir(content)}')
                parts = getattr(content, 'parts', None) or []
                print(f'Parts count: {len(parts)}')
                for i, part in enumerate(parts[:3]):
                    print(f'  Part[{i}] type: {type(part).__name__}')
                    print(f'  Part[{i}] has text: {hasattr(part, "text")}')
                    if hasattr(part, 'text') and part.text:
                        print(f'  Part[{i}] text (first 100 chars): {part.text[:100]}...')

            # Grounding metadata inspection
            gm = getattr(c, 'grounding_metadata', None)
            print(f'\n--- GROUNDING METADATA INSPECTION ---')
            print(f'grounding_metadata: {gm}')
            print(f'grounding_metadata type: {type(gm).__name__ if gm else "None"}')

            if gm:
                print(f'\nGM attributes: {dir(gm)}')
                print(f'Has grounding_chunks: {hasattr(gm, "grounding_chunks")}')
                print(f'Has web_search_queries: {hasattr(gm, "web_search_queries")}')
                print(f'Has grounding_supports: {hasattr(gm, "grounding_supports")}')
                print(f'Has search_entry_point: {hasattr(gm, "search_entry_point")}')

                chunks = getattr(gm, 'grounding_chunks', None) or []
                print(f'\nGrounding chunks count: {len(chunks)}')
                for i, ch in enumerate(chunks[:5]):
                    print(f'  Chunk[{i}] type: {type(ch).__name__}')
                    print(f'  Chunk[{i}] attributes: {dir(ch)}')
                    web = getattr(ch, 'web', None)
                    if web:
                        print(f'    web type: {type(web).__name__}')
                        print(f'    web attributes: {dir(web)}')
                        title = getattr(web, 'title', None)
                        uri = getattr(web, 'uri', None)
                        print(f'    title: {title}')
                        print(f'    uri: {uri}')

                queries = getattr(gm, 'web_search_queries', None) or []
                print(f'\nWeb search queries: {queries}')

                supports = getattr(gm, 'grounding_supports', None) or []
                print(f'Grounding supports count: {len(supports)}')

                sep = getattr(gm, 'search_entry_point', None)
                if sep:
                    print(f'Search entry point: {type(sep).__name__}')
                    rendered = getattr(sep, 'rendered_content', None)
                    if rendered:
                        print(f'Rendered content (first 200 chars): {rendered[:200]}...')
            else:
                print('NO grounding_metadata - THIS IS THE ROOT CAUSE TO INVESTIGATE')
                # Check if there's any other metadata
                print(f'\nAll candidate attributes with values:')
                for attr in dir(c):
                    if not attr.startswith('_'):
                        val = getattr(c, attr, None)
                        if val and not callable(val):
                            print(f'  {attr}: {type(val).__name__}')

        return True
    except Exception as e:
        print(f'Status: FAILED')
        print(f'Error type: {type(e).__name__}')
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()
        return False


def test_ddg_full_inspection():
    """Full inspection of DDG response."""
    print('\n=== TESTING DDG DIRECTLY ===')
    from duckduckgo_search import DDGS

    print(f'DDGS module: {DDGS}')
    print(f'DDGS.__version__ if available: {getattr(DDGS, "__version__", "N/A")}')

    try:
        with DDGS() as ddgs:
            print(f'\nDDGS instance created: {ddgs}')
            print(f'DDGS attributes: {dir(ddgs)}')

            query = "Claude AI"
            print(f'\nCalling ddgs.text with query: {query}, max_results=5')

            raw_results = ddgs.text(query, max_results=5)

            print(f'\nRaw results type: {type(raw_results).__name__}')
            print(f'Raw results: {raw_results}')

            if raw_results:
                print(f'Results count: {len(raw_results)}')
                for i, item in enumerate(raw_results[:5]):
                    print(f'\nItem[{i}] type: {type(item).__name__}')
                    print(f'Item[{i}] keys: {item.keys() if isinstance(item, dict) else "not dict"}')
                    if isinstance(item, dict):
                        for k, v in item.items():
                            print(f'  {k}: {v[:100] if isinstance(v, str) else v}...')
            else:
                print('NO RESULTS - empty iterator returned')
                # Try to see if there's an error
                print('\nAttempting to get debug info...')

    except ImportError as e:
        print(f'ImportError: {e}')
        print('duckduckgo-search library issue')
    except Exception as e:
        print(f'Error: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()


async def main():
    # Test Gemini with full inspection
    await test_gemini_full_inspection('gemma-4-31b-it', 'What is Claude AI?')

    # Test DDG with full inspection
    test_ddg_full_inspection()

asyncio.run(main())