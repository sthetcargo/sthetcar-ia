from pathlib import Path
import argparse
import json
import os
import sys

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description="Migra fotos e estado do V33 local para Supabase.")
    parser.add_argument("--source", required=True, help="Pasta da V33 atual, por exemplo E:\\Sthetcar IA V33")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        raise SystemExit(f"Pasta fonte nÃ£o encontrada: {source}")

    # O .env da instalaÃ§Ã£o antiga fornece a chave secreta sem ser copiado para o pacote.
    load_dotenv(source / ".env", override=True)

    # Importa somente depois de carregar o .env da instalaÃ§Ã£o fonte.
    from app.storage import upload_file, make_remote_ref
    from app.cloud_state import save_cloud_state

    uploaded = 0
    migrated_refs = {}

    for side in ("before", "after"):
        folder = source / "uploads" / side
        if not folder.exists():
            continue
        for path in folder.iterdir():
            if not path.is_file() or path.name == ".gitkeep":
                continue
            remote = f"{side}/{path.name}"
            upload_file(path, remote)
            migrated_refs[f"/uploads/{side}/{path.name}"] = make_remote_ref(remote)
            uploaded += 1

    def rewrite(value):
        if isinstance(value, str):
            return migrated_refs.get(value, value)
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        return value

    state_map = {
        "historico_posts_aprovados.json": "history",
        "fila_aprovacao.json": "approval_queue",
        "instagram_connection.json": "instagram_connection",
        "instagram_publication_draft.json": "publication_draft",
        "posts_descartados.json": "discarded_posts",
        "posts_publicacao_descartados.json": "publication_discarded",
        "publicacoes_instagram.json": "publication_audit",
        "ultimo_post_aprovado.json": "last_approved",
    }

    migrated_states = 0
    generated = source / "generated"
    for filename, key in state_map.items():
        path = generated / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if save_cloud_state(key, rewrite(data)):
            migrated_states += 1
        else:
            raise RuntimeError(f"NÃ£o foi possÃ­vel gravar o estado {key}. Execute primeiro supabase_app_state.sql.")

    print(f"Fotos enviadas para o Supabase: {uploaded}")
    print(f"Estados migrados para o Supabase: {migrated_states}")
    print("MigraÃ§Ã£o concluÃ­da. Nenhum token foi exibido na saÃ­da.")


if __name__ == "__main__":
    main()
