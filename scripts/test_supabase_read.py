import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url=os.getenv("SUPABASE_URL")
key=os.getenv("SUPABASE_SECRET_KEY")
if not url or not key:
    raise SystemExit("SUPABASE_URL/SUPABASE_SECRET_KEY não configurados")
client=create_client(url,key)
rows=client.table("app_state").select("key,data").execute().data or []
print("SUPABASE: OK")
print("app_state: OK")
print("chaves:", ", ".join(sorted(str(r.get("key")) for r in rows)))
for r in rows:
    data=r.get("data")
    kind="null" if data is None else ("array" if isinstance(data,list) else "object" if isinstance(data,dict) else type(data).__name__)
    print(f"- {r.get('key')}: {kind}")
