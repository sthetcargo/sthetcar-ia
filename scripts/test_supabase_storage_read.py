import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SECRET_KEY")
bucket = os.getenv("STHETCAR_STORAGE_BUCKET", "sthetcar-fotos")

if not url or not key:
    raise SystemExit("SUPABASE_URL/SUPABASE_SECRET_KEY não configurados")

client = create_client(url, key)

# Somente leitura: não faz upload, delete, update ou alteração de bucket.
try:
    root = client.storage.from_(bucket).list("", {"limit": 10, "offset": 0})
    print("SUPABASE STORAGE: OK")
    print(f"bucket: {bucket}")
    print(f"itens_raiz_visiveis: {len(root or [])}")

    for item in (root or [])[:10]:
        name = item.get("name", "")
        kind = "pasta/objeto"
        if item.get("metadata") is not None:
            kind = "arquivo"
        print(f"- {name}: {kind}")
except Exception as exc:
    raise SystemExit(f"SUPABASE STORAGE: ERRO\n{exc}")
