import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SECRET_KEY")
test_key = "_v332_test"

if not url or not key:
    raise SystemExit("SUPABASE_URL/SUPABASE_SECRET_KEY não configurados")

client = create_client(url, key)
payload = {
    "test": "V33.2.6",
    "timestamp": datetime.now(timezone.utc).isoformat(),
}

try:
    # 1) Escreve SOMENTE a chave temporária.
    client.table("app_state").upsert(
        {"key": test_key, "data": payload},
        on_conflict="key",
    ).execute()

    # 2) Lê de volta.
    result = (
        client.table("app_state")
        .select("key,data")
        .eq("key", test_key)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise RuntimeError("A chave de teste não foi encontrada após a gravação.")

    data = rows[0].get("data")
    if not isinstance(data, dict) or data.get("test") != "V33.2.6":
        raise RuntimeError("O conteúdo retornado não corresponde ao payload de teste.")

    print("SUPABASE WRITE/READ: OK")

finally:
    # 3) Remove SOMENTE a chave temporária, mesmo se a leitura falhar.
    try:
        client.table("app_state").delete().eq("key", test_key).execute()
    except Exception as cleanup_error:
        print("ATENÇÃO: falha ao remover _v332_test:")
        print(cleanup_error)
        raise

# 4) Confirma que foi removida.
verify = (
    client.table("app_state")
    .select("key")
    .eq("key", test_key)
    .limit(1)
    .execute()
)
if verify.data:
    raise SystemExit("ERRO: _v332_test ainda existe após a limpeza.")

print("SUPABASE CLEANUP: OK")
print("chave_testada: _v332_test")
print("chaves_reais: não alteradas")
