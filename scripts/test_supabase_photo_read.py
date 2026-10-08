import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SECRET_KEY")
bucket = os.getenv("STHETCAR_STORAGE_BUCKET", "sthetcar-fotos")
folder = os.getenv("STHETCAR_TEST_STORAGE_FOLDER", "before")

if not url or not key:
    raise SystemExit("SUPABASE_URL/SUPABASE_SECRET_KEY não configurados")

client = create_client(url, key)

try:
    items = client.storage.from_(bucket).list(folder, {"limit": 20, "offset": 0})
    files = [
        x.get("name") for x in (items or [])
        if x.get("name") and x.get("metadata") is not None
    ]

    if not files:
        raise SystemExit(
            f"Nenhum arquivo encontrado em {bucket}/{folder}. "
            "Nenhum dado foi alterado."
        )

    object_name = files[0]
    object_path = f"{folder}/{object_name}"

    # Somente leitura: cria uma URL assinada temporária, sem alterar o objeto.
    signed = client.storage.from_(bucket).create_signed_url(object_path, 60)

    if not signed:
        raise SystemExit("Storage acessível, mas não foi possível gerar URL assinada.")

    print("SUPABASE PHOTO READ: OK")
    print(f"bucket: {bucket}")
    print(f"pasta: {folder}")
    print("arquivo: encontrado")
    print("url_assinada: gerada (não exibida)")
    print("duracao: 60 segundos")
except Exception as exc:
    raise SystemExit(f"SUPABASE PHOTO READ: ERRO\n{exc}")
