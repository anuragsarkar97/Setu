import os

from openai import AsyncOpenAI

openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
