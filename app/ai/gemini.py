import os
import json
import difflib
import re
import logging
import time
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

logger = logging.getLogger(__name__)

# Ordem pensada para manter o MVP no nível gratuito.
# Se um modelo estiver temporariamente indisponível (503), o próximo é tentado.
DEFAULT_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
]

# Gemini structured output: the detector must return one validated entry per image.
# Schema mode reduces free-form answers but callers still validate every field and
# fail closed if a plate cannot be localized with confidence.
PLATE_DETECTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "plates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "x1": {"type": "number"},
                                "y1": {"type": "number"},
                                "x2": {"type": "number"},
                                "y2": {"type": "number"},
                            },
                            "required": ["x1", "y1", "x2", "y2"],
                        },
                    },
                    "needs_review": {"type": "boolean"},
                },
                "required": ["index", "plates", "needs_review"],
            },
        },
    },
    "required": ["images"],
}

def _client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY não configurada. "
            "Crie um arquivo .env a partir de .env.example."
        )
    # Explicitly cap each outbound API call so a stalled HTTPS read cannot leave
    # the Gunicorn worker blocked indefinitely. The timeout is specified in ms.
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=60_000),
    )

def _media_part(path: Path):
    ext = path.suffix.lower()
    image_mimes = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }
    video_mimes = {
        ".mp4": "video/mp4", ".mov": "video/quicktime",
    }
    mime = image_mimes.get(ext) or video_mimes.get(ext)
    if not mime:
        raise RuntimeError(f"Tipo de mídia não suportado pela IA: {path.name}")
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)


def _image_part(path: Path):
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)


def _models_to_try():
    configured = os.getenv("GEMINI_MODEL", "").strip()
    models = [configured] if configured else []
    for model in DEFAULT_MODELS:
        if model not in models:
            models.append(model)
    return models

def _response_text(response):
    """Extract text robustly from the Gemini response object."""
    text = getattr(response, "text", None)
    if text and str(text).strip():
        return str(text).strip()

    chunks = []
    for candidate in (getattr(response, "candidates", None) or []):
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) or []):
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(str(part_text))
    return "\n".join(chunks).strip()


def _fallback_caption(analysis: str, vehicle: str = "", service: str = ""):
    """Local fallback so the approval screen never receives an empty caption."""
    vehicle_text = vehicle or "seu veículo"
    service_text = service or "este atendimento"
    return (
        f"✨ Resultado de mais um atendimento na Sthetcar.\n\n"
        f"Realizamos {service_text} em {vehicle_text}, com atenção aos detalhes "
        f"e ao acabamento final.\n\n"
        f"Quer deixar seu carro em outro nível? Fale com a Sthetcar e agende seu atendimento.\n\n"
        f"#Sthetcar #EsteticaAutomotiva #DetalhamentoAutomotivo "
        f"#CarDetailing #AntesEDepois"
    )


def _generation_config(
    model,
    temperature,
    max_output_tokens,
    response_mime_type=None,
    response_schema=None,
):
    """Build model-compatible generation settings without network access.

    Gemini 3.x and 4.x require sampling parameters to remain at API defaults;
    older model families retain the application's existing temperature tuning.
    Optional structured-output settings are used for machine-parsed responses.
    """
    normalized_model = str(model or "").strip().lower().removeprefix("models/")
    options = {"max_output_tokens": max_output_tokens}
    if not normalized_model.startswith(("gemini-3", "gemini-4")):
        options["temperature"] = temperature
    if response_mime_type:
        options["response_mime_type"] = response_mime_type
    if response_schema is not None:
        options["response_schema"] = response_schema
    return types.GenerateContentConfig(**options)


def _generate(
    client,
    contents,
    temperature,
    max_output_tokens,
    response_mime_type=None,
    response_schema=None,
):
    last_error = None
    for model in _models_to_try():
        started_at = time.monotonic()
        logger.info("Gemini generation started model=%s", model)
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=_generation_config(
                    model,
                    temperature,
                    max_output_tokens,
                    response_mime_type=response_mime_type,
                    response_schema=response_schema,
                ),
            )
            logger.info(
                "Gemini generation completed model=%s elapsed_seconds=%.1f",
                model,
                time.monotonic() - started_at,
            )
            return response, model
        except Exception as exc:
            elapsed = time.monotonic() - started_at
            last_error = exc
            logger.warning(
                "Gemini generation failed model=%s elapsed_seconds=%.1f error_type=%s",
                model,
                elapsed,
                type(exc).__name__,
            )
            message = str(exc)
            # Só fazemos fallback automático para erros transitórios/indisponibilidade.
            if not any(code in message for code in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "INTERNAL")):
                raise
    raise RuntimeError(
        "O Gemini está temporariamente indisponível em todos os modelos tentados. "
        "Tente novamente em alguns minutos. "
        f"Último erro: {last_error}"
    )

def analyze_before_after(before_paths, after_paths,
                         vehicle: str = "", service: str = "", notes: str = ""):
    client = _client()

    prompt = f"""
    Você é o analista visual da Sthetcar Estética Automotiva.

    Compare cuidadosamente o conjunto de imagens ANTES com o conjunto de imagens DEPOIS.
    As imagens são pares de referência do mesmo serviço; use todos os ângulos disponíveis.
    Identifique somente características visualmente observáveis.
    NÃO invente serviços, produtos, técnicas ou resultados.

    Dados fornecidos pelo usuário:
    Veículo: {vehicle or "não informado"}
    Serviço informado: {service or "não informado"}
    Observações: {notes or "nenhuma"}

    Responda em português do Brasil, com:
    1. RESUMO DA TRANSFORMAÇÃO
    2. O QUE APARECE NO ANTES
    3. O QUE APARECE NO DEPOIS
    4. VEÍCULO IDENTIFICADO: informe marca/modelo somente se estiver visualmente claro; caso contrário, escreva "Não identificado".
    5. SERVIÇO INFORMADO / SERVIÇO IDENTIFICÁVEL
    6. CONFIANÇA: baixa, média ou alta
    7. ALERTAS

    Se o serviço não puder ser determinado pela imagem, escreva claramente:
    "Não foi possível determinar o serviço somente pelas imagens."
    """

    contents = [prompt]
    contents.append(f"=== FOTOS ANTES ({len(before_paths)}) ===")
    for i, path in enumerate(before_paths, 1):
        label = "VÍDEO" if path.suffix.lower() in {".mp4", ".mov"} else "FOTO"
        contents.extend([f"{label} ANTES {i}", _media_part(path)])
    contents.append(f"=== FOTOS DEPOIS ({len(after_paths)}) ===")
    for i, path in enumerate(after_paths, 1):
        label = "VÍDEO" if path.suffix.lower() in {".mp4", ".mov"} else "FOTO"
        contents.extend([f"{label} DEPOIS {i}", _media_part(path)])

    response, _ = _generate(
        client, contents, temperature=0.4, max_output_tokens=1400
    )
    return _response_text(response) or "A IA não retornou uma análise."


def _looks_like_caption(text: str):
    """Reject prompt leakage, analysis bullets, or obviously truncated copy."""
    t = (text or "").strip()
    if len(t) < 280:
        return False
    low = t.casefold()
    bad_markers = [
        "resumo da transformação", "o que aparece no antes", "o que aparece no depois",
        "confiança:", "alertas", "explanation of service", "benefits:",
        "quebradas que encobriam refletores", "- benefits", "- explanation of service",
        "legenda:", "[legenda completa]",
    ]
    if any(marker in low for marker in bad_markers):
        return False
    # A commercial caption should normally contain paragraph breaks and a call to action.
    # Aceite também respostas com parágrafos simples para não descartar uma boa reescrita.
    if t.count("\n\n") < 1 and len(t) < 420:
        return False
    cta_markers = ("entre em contato", "fale com", "solicite", "agende", "chame no direct", "envie uma mensagem")
    if not any(marker in low for marker in cta_markers):
        return False
    # Reject outputs that are mostly bullets or English instruction fragments.
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    bullet_lines = sum(1 for x in lines if x.startswith(("-", "•", "* ")))
    if bullet_lines >= 2:
        return False
    return True


def _caption_similarity(a: str, b: str) -> float:
    """Compare only the caption body, ignoring hashtags and small formatting changes."""
    def body(value):
        value = (value or "").strip()
        value = re.split(r"\n\s*HASHTAGS?\s*:\s*", value, maxsplit=1, flags=re.IGNORECASE)[0]
        value = re.sub(r"\s+", " ", value).strip().casefold()
        return value
    aa, bb = body(a), body(b)
    if not aa or not bb:
        return 0.0
    return difflib.SequenceMatcher(None, aa, bb).ratio()


def _same_opening(a: str, b: str) -> bool:
    """Detect when the rewrite kept essentially the same opening sentence."""
    def first_sentence(value):
        value = re.split(r"\n\s*HASHTAGS?\s*:", (value or ""), maxsplit=1, flags=re.IGNORECASE)[0]
        value = re.sub(r"\s+", " ", value).strip().casefold()
        return re.split(r"(?<=[.!?])\s+", value, maxsplit=1)[0][:220]
    aa, bb = first_sentence(a), first_sentence(b)
    return bool(aa and bb and difflib.SequenceMatcher(None, aa, bb).ratio() >= 0.78)


def rewrite_caption(analysis: str, vehicle: str = "", service: str = "",
                    current_caption: str = "", rewrite_count: int = 1, rewrite_angle: str = ""):
    """Create a genuinely new caption from the current caption, not a repeated copy."""
    client = _client()
    source_caption = (current_caption or "").strip()
    variation = ((int(rewrite_count or 1) - 1) % 6) + 1
    strategies = {
        1: "Foco no problema/dor do cliente: abra com uma situação que o proprietário reconheceria no próprio carro e conduza para a solução.",
        2: "Foco no processo técnico: abra explicando o trabalho realizado e destaque preparação, lixamento e polimento quando forem pertinentes às fotos e ao serviço.",
        3: "Foco na transformação ANTES x DEPOIS: abra pelo contraste visual observado e mostre o que mudou no resultado.",
        4: "Foco nos benefícios: destaque primeiro o que o cliente ganha com o serviço, separando benefício estético de benefício funcional plausível.",
        5: "Foco educativo: ensine de forma simples por que o problema aparece e por que o tratamento realizado faz diferença, sem inventar causas ou procedimentos específicos.",
        6: "Foco comercial: abra de forma envolvente, apresente o caso e conduza naturalmente para uma CTA de orçamento/agendamento.",
    }
    angle_map = {
        "problema_cliente": "Foco no problema/dor do cliente: abra com uma situação que o proprietário reconheceria no próprio carro e conduza para a solução.",
        "processo_tecnico": "Foco no processo técnico: abra explicando o trabalho realizado e destaque preparação, lixamento e polimento quando forem pertinentes às fotos e ao serviço.",
        "transformacao_antes_depois": "Foco na transformação ANTES x DEPOIS: abra pelo contraste visual observado e mostre o que mudou no resultado.",
        "beneficios": "Foco nos benefícios: destaque primeiro o que o cliente ganha com o serviço, separando benefício estético de benefício funcional plausível.",
        "educativa": "Foco educativo: ensine de forma simples por que o problema aparece e por que o tratamento realizado faz diferença, sem inventar causas ou procedimentos específicos.",
        "comercial": "Foco comercial: abra de forma envolvente, apresente o caso e conduza naturalmente para uma CTA de orçamento/agendamento.",
    }
    selected_angle = (rewrite_angle or "").strip()
    strategy = angle_map.get(selected_angle) or strategies[variation]

    prompt = f"""
Você é o redator comercial da Sthetcar Estética Automotiva.

TAREFA: CRIE UMA NOVA VERSÃO da legenda atual abaixo.

ATENÇÃO: esta é a reescrita número {rewrite_count}. A resposta NÃO pode ser uma cópia,
uma repetição ou uma paráfrase frase por frase da legenda atual. Escreva uma legenda nova
A PARTIR DOS FATOS, mudando de verdade a abertura, a construção das frases e a ordem das
ideias. Não reutilize a primeira frase da legenda atual.

O usuário escolheu este ângulo para esta nova versão:
{strategy}

O ângulo escolhido é o FOCO PRINCIPAL, não uma obrigação de excluir os demais elementos.
Ainda inclua, quando pertinentes, transformação, processo técnico, benefício e CTA. Apenas
mude a prioridade e a forma de contar a história para que esta versão seja claramente
diferente das anteriores.

A legenda atual é uma fonte de fatos, não um molde textual. Preserve o que é útil:
problema observado, serviço realizado, transformação, processo técnico, benefícios e CTA.
Você pode reorganizar essas informações livremente.

REGRAS:
- Português do Brasil, natural, profissional e comercial.
- Mantenha veículo e serviço informados.
- Para revitalização/restauração de faróis, pode mencionar preparação, lixamento e polimento como etapas usuais, sem inventar produto, marca, granulação ou procedimento específico.
- Pode mencionar melhor aproveitamento da iluminação e mais clareza ao dirigir à noite, mas não prometa aumento de luminosidade nem segurança garantida.
- A CTA deve convidar quem tem o mesmo problema a falar com a Sthetcar, pedir orçamento ou agendar atendimento.
- Não use títulos, listas ou bullets.
- Não use frases genéricas como substitutas do conteúdo: "resultado de mais um atendimento", "atenção aos detalhes", "deixe seu carro em outro nível".
- Evite absolutos e garantias como "100%", "perfeito", "como novo", "recuperou totalmente" e "segurança garantida".
- Aproximadamente 100 a 180 palavras, em 3 a 5 parágrafos curtos.
- Não coloque hashtags dentro da legenda.

VEÍCULO: {vehicle or "não informado"}
SERVIÇO: {service or "não informado"}

=== LEGENDA ATUAL — NÃO COPIAR ===
{source_caption or "Não há legenda atual; use a análise visual como fonte de fatos."}

=== ANÁLISE VISUAL DE REFERÊNCIA ===
{analysis}

ANTES DE RESPONDER, confira internamente:
- A primeira frase é diferente da legenda atual?
- A ordem das ideias mudou?
- A redação está claramente diferente, sem perder os fatos importantes?
- Existe uma CTA concreta?

FORMATO:
[3 a 5 parágrafos da nova legenda]

HASHTAGS:
#... #... #... #... #... #... #...
"""

    # Faz até três tentativas com instruções progressivamente mais fortes.
    attempts = [
        (prompt, 0.65),
        (prompt + f"\n\nIMPORTANTE: a legenda atual começa com: {source_caption[:180]!r}. NÃO comece com essa frase nem com uma simples paráfrase dela. Reestruture o texto por completo.", 0.78),
        (prompt + "\n\nÚLTIMA TENTATIVA: escreva do zero usando somente os fatos da legenda e da análise. Não faça substituição palavra por palavra. Mude radicalmente a abertura e a sequência dos argumentos.", 0.9),
    ]

    best = ""
    best_similarity = 1.0
    for attempt_prompt, temperature in attempts:
        response, _ = _generate(client, attempt_prompt, temperature=temperature, max_output_tokens=1400)
        text = _response_text(response)
        if not text or not _looks_like_caption(text):
            continue
        similarity = _caption_similarity(text, source_caption)
        opening_same = _same_opening(text, source_caption)
        if similarity < best_similarity:
            best, best_similarity = text, similarity
        # 0.72 permite preservar os fatos sem aceitar uma simples repetição.
        if not source_caption or (similarity < 0.72 and not opening_same):
            return text

    # Se todas as tentativas forem parecidas, faça uma chamada final explicitamente orientada
    # a uma estrutura diferente. Nunca devolva o fallback genérico de atendimento.
    emergency = f"""
Escreva uma legenda NOVA para Instagram da Sthetcar usando os fatos abaixo.
NÃO reescreva frase por frase a legenda anterior. NÃO repita a abertura.
A nova versão deve ter 100-180 palavras, 3-5 parágrafos, linguagem comercial natural e CTA.
Estruture nesta ordem: transformação/resultados visíveis -> como o serviço foi realizado -> benefício para quem tem esse problema -> convite para orçamento.
Preserve os fatos concretos e o serviço. Para faróis, se pertinente, mencione lixamento e polimento e que a recuperação das lentes pode contribuir para melhor aproveitamento da iluminação e clareza ao dirigir à noite, sem prometer segurança ou aumento de luminosidade.

Veículo: {vehicle or 'não informado'}
Serviço: {service or 'não informado'}

Legenda anterior, que NÃO deve ser copiada:
{source_caption}

Análise visual:
{analysis}

Termine com:
HASHTAGS:
seguido de 7-10 hashtags.
"""
    response, _ = _generate(client, emergency, temperature=0.95, max_output_tokens=1400)
    text = _response_text(response)
    if text and _looks_like_caption(text):
        similarity = _caption_similarity(text, source_caption)
        opening_same = _same_opening(text, source_caption)
        if not source_caption or (similarity < 0.78 and not opening_same):
            return text
        if similarity < best_similarity:
            best, best_similarity = text, similarity

    # Use the best valid, meaningfully different response from the earlier attempts
    # rather than silently returning the old caption and reporting a false success.
    if best and best_similarity < 0.86 and not _same_opening(best, source_caption):
        return best

    if source_caption:
        raise RuntimeError(
            "A IA não conseguiu produzir uma legenda suficientemente diferente da atual. "
            "A legenda original foi preservada; tente outro foco ou tente novamente."
        )

    # There is no current caption to preserve, so a factual fallback is appropriate.
    return _fallback_caption(analysis, vehicle, service)


def generate_post(analysis: str, vehicle: str = "",
                  service: str = "", notes: str = ""):
    client = _client()

    prompt = f"""
    Você é o redator oficial da Sthetcar Estética Automotiva.

    Escreva uma publicação de Instagram realmente específica para este atendimento.
    Use SOMENTE fatos presentes na análise visual e nos campos fornecidos.

    REGRAS OBRIGATÓRIAS:
    - Português do Brasil, natural e profissional.
    - Não comece com frases genéricas como "resultado que fala por si",
      "mais um atendimento", "transformação incrível" ou equivalentes.
    - A primeira frase deve destacar a transformação observável no veículo.
    - Cite 2 ou 3 detalhes concretos que apareçam no ANTES e/ou no DEPOIS.
    - Explique brevemente o que o serviço realizado entregou e use conhecimento técnico geral da estética automotiva quando isso ajudar a explicar o serviço.
    - Se o serviço informado pelo usuário for explicitamente um serviço de restauração/revitalização de faróis, você pode mencionar etapas normalmente associadas a esse tipo de trabalho, como preparação, lixamento e polimento, mas não invente produtos, marcas, granulações ou etapas específicas que não tenham sido informadas.
    - Mostre tanto o benefício visual quanto, quando tecnicamente plausível, o benefício funcional relacionado ao serviço. Em faróis, por exemplo, pode mencionar que a recuperação das lentes pode contribuir para melhor aproveitamento da iluminação e mais clareza ao dirigir à noite, mas não prometa aumento de luminosidade nem segurança garantida sem medição técnica.
    - A CTA é OBRIGATÓRIA e deve convidar o cliente a se identificar com o problema mostrado e entrar em contato. Exemplo de estrutura: "Se os faróis do seu carro também estão amarelados, foscos ou desgastados, fale com a Sthetcar e solicite seu orçamento." Não copie sempre a mesma frase; adapte ao serviço.
    - Não invente marca, modelo, produto, preço ou resultado específico.
    - Evite absolutos e garantias publicitárias como "100%", "perfeito", "como novo", "recuperou totalmente" ou "segurança garantida". Se a transformação estiver muito clara nas fotos, pode usar uma descrição comercial forte, mas proporcional e verificável, como "melhora evidente", "recuperação visual" ou "maior transparência aparente".
    - Não transforme uma descrição visual em uma garantia técnica. Diferencie "melhora visual" de "benefício funcional".
    - Se o usuário forneceu o nome do serviço, respeite esse serviço como fato informado pelo cliente e use-o como base da narrativa.
    - Se o veículo não estiver identificado com segurança, não invente um modelo.
    - A legenda deve ter aproximadamente 100 a 180 palavras e 3 a 5 parágrafos curtos.
    - A estrutura ideal é: problema percebido → serviço/transformação → benefícios para o cliente → CTA.
    - Gere de 7 a 10 hashtags relevantes, específicas para o serviço e, somente se seguro, para o veículo.
    - Não coloque hashtags dentro da legenda.

    Veículo informado: {vehicle or "não informado"}
    Serviço informado: {service or "não informado"}
    Observações: {notes or "nenhuma"}

    ANÁLISE VISUAL:
    {analysis}

    FORMATO EXATO:
    LEGENDA:
    [legenda completa]

    HASHTAGS:
    [hashtags separadas por espaço]
    """

    response, _ = _generate(
        client,
        prompt,
        temperature=0.55,
        max_output_tokens=1200,
    )
    text = _response_text(response)
    if text:
        return text

    retry_prompt = f"""
    Reescreva uma legenda profissional e específica para Instagram para a Sthetcar.
    Não use frases genéricas. Combine a transformação visual observada com o serviço informado pelo usuário.
    Para serviços de restauração/revitalização de faróis, é aceitável mencionar de forma geral etapas usuais como lixamento e polimento, sem inventar produtos ou detalhes não informados.
    Pode mencionar benefícios funcionais plausíveis, como contribuição para melhor aproveitamento da iluminação e mais clareza ao dirigir à noite, mas sem prometer segurança ou aumento de luminosidade como garantia.
    Evite absolutos e garantias publicitárias.
    Use aproximadamente 100 a 170 palavras, 3 a 5 parágrafos e finalize obrigatoriamente com uma CTA que convide quem tem o mesmo problema a entrar em contato para orçamento.
    Depois escreva HASHTAGS: seguido de 7 hashtags relevantes.

    Veículo: {vehicle or "não informado"}
    Serviço: {service or "não informado"}
    Análise visual:
    {analysis}
    """
    retry_response, _ = _generate(
        client, retry_prompt, temperature=0.45, max_output_tokens=900
    )
    text = _response_text(retry_response)
    return text or _fallback_caption(analysis, vehicle, service)



def _parse_plate_detection_json(raw):
    """Parse the detector's structured JSON response without guessing content."""
    text = (raw or "").strip()
    if not text:
        raise RuntimeError("O detector de placas não retornou uma resposta.")
    # Tolerate a Markdown-fenced JSON object from older/model-specific responses,
    # but don't extract arbitrary braces from natural-language prose.
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    else:
        text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text, flags=re.IGNORECASE).strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("O detector de placas retornou um formato inválido.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("O detector de placas retornou um objeto JSON inválido.")
    return payload


def _plate_response_status(response):
    """Return non-sensitive API status metadata; never log prompts or image contents."""
    candidates = getattr(response, "candidates", None) or []
    finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    prompt_feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(prompt_feedback, "block_reason", None) if prompt_feedback else None
    return (
        str(getattr(finish_reason, "name", finish_reason) or "unknown")[:80],
        str(getattr(block_reason, "name", block_reason) or "none")[:80],
    )


def detect_license_plates(paths):
    """Return normalized bounding boxes for every visible license plate in each image.

    The model is asked to localize plates, not to read or transcribe their characters.
    This is a privacy aid, not a guarantee; callers must show a reviewable preview.
    """
    paths = [Path(path) for path in paths]
    if not paths:
        return []

    client = _client()
    prompt = """
Você é um detector visual de placas de identificação veicular para proteção de privacidade.
Analise CADA imagem anexada, separadamente. Não transcreva, não reconheça por OCR e não
retorne letras ou números da placa.

Retorne SOMENTE JSON válido neste formato:
{
  "images": [
    {
      "index": 1,
      "plates": [{"x1": 100, "y1": 200, "x2": 400, "y2": 300}],
      "needs_review": false
    }
  ]
}

Regras:
- Deve haver exatamente uma entrada para cada imagem fornecida, com seu índice indicado antes dela.
- Coordenadas normalizadas de 0 a 1000: x1/y1 são o canto superior esquerdo e x2/y2 o inferior direito.
- Cada caixa deve cobrir a placa inteira e sua moldura, com uma pequena margem externa.
- Inclua TODAS as placas veiculares visíveis, mesmo pequenas, inclinadas, distantes ou parcialmente ocultas.
- Se houver uma área que possa ser uma placa mas não for possível localizar a caixa com confiança,
  marque needs_review=true e inclua a caixa mais conservadora possível, quando conseguir.
- Use needs_review=false somente quando estiver confiante de que as placas visíveis foram localizadas
  ou de que não há placa veicular visível na imagem.
- Se a imagem estiver desfocada, cortada, escura, refletida ou ambígua e isso impedir excluir uma placa,
  marque needs_review=true.
- Não invente placas em objetos que claramente não sejam veículos.
"""
    contents = [prompt]
    for index, path in enumerate(paths, 1):
        contents.extend([f"IMAGEM {index} (índice obrigatório: {index})", _image_part(path)])

    response, model = _generate(
        client,
        contents,
        temperature=0,
        # Each request handles at most three photos; reserve enough output room
        # for complete coordinates even if many plates are visible.
        max_output_tokens=4096,
        response_mime_type="application/json",
        response_schema=PLATE_DETECTION_RESPONSE_SCHEMA,
    )
    raw = _response_text(response)
    try:
        payload = _parse_plate_detection_json(raw)
    except RuntimeError:
        finish_reason, block_reason = _plate_response_status(response)
        # Never log raw model text, prompts, images, plate numbers, or credentials.
        logger.warning(
            "Plate detector returned invalid structured output model=%s text_chars=%d finish_reason=%s prompt_block_reason=%s",
            model,
            len(raw),
            finish_reason,
            block_reason,
        )
        raise

    entries = payload.get("images") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or len(entries) != len(paths):
        raise RuntimeError("A detecção não conferiu todas as fotos enviadas.")

    by_index = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("A detecção de placas retornou um item inválido.")
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("A detecção de placas não identificou a foto correspondente.") from exc
        if index < 1 or index > len(paths) or index in by_index:
            raise RuntimeError("A detecção de placas retornou índices duplicados ou inválidos.")
        plates = entry.get("plates")
        needs_review = entry.get("needs_review")
        if not isinstance(plates, list) or not isinstance(needs_review, bool):
            raise RuntimeError("A detecção de placas retornou campos incompletos.")
        checked_plates = []
        for box in plates:
            if not isinstance(box, dict):
                raise RuntimeError("A detecção retornou uma caixa de placa inválida.")
            try:
                coords = {key: float(box[key]) for key in ("x1", "y1", "x2", "y2")}
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("A detecção retornou coordenadas de placa incompletas.") from exc
            if any(not 0 <= value <= 1000 for value in coords.values()):
                raise RuntimeError("A detecção retornou coordenadas fora da imagem.")
            if coords["x2"] <= coords["x1"] or coords["y2"] <= coords["y1"]:
                raise RuntimeError("A detecção retornou uma caixa de placa sem área.")
            checked_plates.append(coords)
        by_index[index] = {
            "index": index,
            "plates": checked_plates,
            "needs_review": needs_review,
        }

    if set(by_index) != set(range(1, len(paths) + 1)):
        raise RuntimeError("A detecção de placas deixou fotos sem verificação.")
    return [by_index[index] for index in range(1, len(paths) + 1)]
