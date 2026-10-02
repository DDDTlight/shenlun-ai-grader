import tenacity
from pathlib import Path
from dotenv import dotenv_values
from openai import OpenAI

ENV_PATH = Path(__file__).resolve().parent / ".env"
config = dotenv_values(ENV_PATH)
api_key = config.get("DEEPSEEK_API_KEY")
if not api_key:
    raise ValueError("未在 .env 中找到 DEEPSEEK_API_KEY")

client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com"
)

def chat(system:str,user:str,thinking:bool,json_mode:bool=False) -> str:
    resp = client.chat.completions.create(
        model="deepseek-falsh",
        messages=[
            {"role":"system","content":system},
            {"role":"user","content":user}
        ],
        reasoning_effort="high",
        extra_body={"thinking":{"type":"enabled" if thinking else "disabled"}},
        temperature=0.1,
        max_tokens=8192
    )
    return resp.choices[0].message.content

chat1 = chat("你是一个傲娇猫娘，要以傲娇猫娘的口气说话","告诉我你是谁",True)
print(chat1)








