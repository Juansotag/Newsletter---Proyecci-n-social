import asyncio
import anthropic
import os
import httpx

async def main():
    print("Initializing client...")
    client = anthropic.AsyncAnthropic(api_key="", http_client=httpx.AsyncClient(verify=False))
    try:
        print("Calling messages.create...")
        await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=10,
            messages=[{"role": "user", "content": "Hi"}]
        )
    except Exception as e:
        print("Caught error:", type(e), e)

asyncio.run(main())
