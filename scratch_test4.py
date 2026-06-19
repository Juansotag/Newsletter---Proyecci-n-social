import asyncio
import os
from dotenv import load_dotenv
from backend.run_due import generate_once as _gen

async def test():
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    print("API KEY from env:", api_key)
    try:
        res = await _gen({}, api_key=api_key)
        print("GEN RES:", res)
    except Exception as e:
        print("GEN ERROR:", type(e), e)

asyncio.run(test())
