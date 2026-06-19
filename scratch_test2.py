import anthropic
import os

print("Anthropic version:", anthropic.__version__)

try:
    client = anthropic.AsyncAnthropic(api_key="")
except Exception as e:
    print("Empty string error:", type(e), e)

try:
    client = anthropic.AsyncAnthropic(api_key="null")
except Exception as e:
    print("Null string error:", type(e), e)

try:
    client = anthropic.AsyncAnthropic(api_key=None)
except Exception as e:
    print("None key error:", type(e), e)
