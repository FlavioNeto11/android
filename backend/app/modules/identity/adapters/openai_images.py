"""Gerador de imagem pela API de Imagens da OpenAI (`/v1/images/generations` e `/v1/images/edits`).

Porta `ImageGenerator` própria, FORA dos cinco papéis de IA: o saldo da Anthropic não compra imagem, então esta é
outra conta, outra chave (`OPENAI_API_KEY`, lida por `EnvSettings` como `SecretStr` — nunca por `os.environ` solto)
e outro teto. Custo pelo `usage` da resposta (tokens de texto, de imagem de entrada e de saída) × o preço por token
declarado em `ai.image.price_per_mtok` (Fase 17): o `gpt-image-2` publica preço por token, não por imagem. Sem
`usage` na resposta, ou sem preço por token, vale o preço por imagem DECLARADO (`ai.image.price_per_image`) — "sem
preço" aqui viraria gasto invisível.

O que sai da máquina: o prompt (só atributos, nunca o nome da persona) e, a partir da segunda imagem, a imagem
principal como referência (`/images/edits`) para o rosto se manter. Recusa do filtro de conteúdo (`content_policy`,
`moderation`, `safety` num 400) sobe como `GeracaoRecusada` — vira `refused`, nunca uma imagem simulada no lugar.
Erros mapeados como em `planning/openai_provider.py`. `httpx.MockTransport` entra por `set_client`, como lá.
"""
from __future__ import annotations

import base64
import time
from collections.abc import Mapping

import httpx

from app.modules.identity.adapters.pos_processamento import pos_processar
from app.modules.identity.application.ports import GeneratedImage
from app.modules.identity.domain.persona_image import GeracaoFalhou, GeracaoRecusada, PersonaImageSpec

URL_PADRAO = "https://api.openai.com/v1"
_MARCAS_DE_RECUSA = ("content_policy", "moderation", "safety", "content policy")


class OpenAIImageGenerator:
    name = "openai"
    simulated = False
    sends_data_externally = True

    def __init__(self, *, api_key: str | None, model: str = "gpt-image-2", quality: str = "medium",
                 price_per_image: Mapping[str, float] | None = None,
                 price_per_mtok: Mapping[str, float] | None = None, timeout_s: float = 180.0,
                 base_url: str = URL_PADRAO) -> None:
        self.model = model
        self.quality = quality
        self._key = (api_key or "").strip()
        self._precos = dict(price_per_image or {})
        self._por_token = dict(price_per_mtok or {})
        self._timeout = timeout_s
        self._base = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._key)

    @property
    def price(self) -> float:
        """O preço da qualidade configurada. Sem preço declarado, o MAIS CARO da tabela — nunca zero (a regra de
        `planning/costs.py`)."""
        if self.quality in self._precos:
            return float(self._precos[self.quality])
        return max((float(v) for v in self._precos.values()), default=0.0)

    def custo(self, corpo: object) -> float:
        """US$ desta geração pelo `usage` da resposta; sem `usage` (ou sem preço por token), o preço declarado.

        `input_tokens_details` separa texto de imagem de entrada — a referência da segunda imagem em diante é cobrada
        como imagem. Sem o detalhe, a entrada inteira conta como imagem, a tarifa mais cara das duas.
        """
        uso = corpo.get("usage") if isinstance(corpo, dict) else None
        if not self._por_token or not isinstance(uso, dict):
            return self.price
        entrada = int(uso.get("input_tokens") or 0)
        saida = int(uso.get("output_tokens") or 0)
        detalhe = uso.get("input_tokens_details")
        if isinstance(detalhe, dict) and ("text_tokens" in detalhe or "image_tokens" in detalhe):
            texto = int(detalhe.get("text_tokens") or 0)
            imagem = int(detalhe.get("image_tokens") or 0)
        else:
            texto, imagem = 0, entrada
        p = self._por_token
        usd = (texto * float(p.get("text_in", 0.0)) + imagem * float(p.get("image_in", 0.0))
               + saida * float(p.get("output", 0.0))) / 1_000_000
        return round(usd, 6)

    def set_client(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def generate(self, spec: PersonaImageSpec, *, reference: bytes | None = None) -> GeneratedImage:
        if not self.configured:
            raise GeracaoFalhou("Provedor de imagem sem chave (OPENAI_API_KEY ausente no .env).", kind="not_configured")
        largura, altura = spec.provider_size
        tamanho = f"{largura}x{altura}"
        cabecalhos = {"authorization": f"Bearer {self._key}"}
        t0 = time.monotonic()
        try:
            if reference is None:
                resposta = await self._http().post(
                    f"{self._base}/images/generations", headers=cabecalhos,
                    json={"model": self.model, "prompt": spec.prompt, "size": tamanho, "quality": self.quality,
                          "n": 1, "output_format": "png"})
            else:
                # A principal vai como referência: mesmo rosto nas seguintes. Multipart, como a API pede.
                resposta = await self._http().post(
                    f"{self._base}/images/edits", headers=cabecalhos,
                    data={"model": self.model, "prompt": spec.prompt, "size": tamanho, "quality": self.quality,
                          "n": "1", "output_format": "png"},
                    files={"image": ("reference.jpg", reference, "image/jpeg")})
        except httpx.TimeoutException as exc:
            raise GeracaoFalhou("Tempo esgotado ao contatar a API de imagens.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise GeracaoFalhou("Falha de rede ao contatar a API de imagens.", retryable=True) from exc
        self._raise_for_status(resposta)
        try:
            corpo = resposta.json()
        except ValueError as exc:
            raise GeracaoFalhou("Resposta não-JSON da API de imagens.", retryable=True) from exc
        itens = corpo.get("data") if isinstance(corpo, dict) else None
        b64 = itens[0].get("b64_json") if isinstance(itens, list) and itens and isinstance(itens[0], dict) else None
        if not isinstance(b64, str) or not b64:
            raise GeracaoFalhou("A API de imagens respondeu sem imagem.", retryable=True)
        original = base64.b64decode(b64)
        dados, w, h = pos_processar(original, spec)
        return GeneratedImage(data=dados, mime="image/jpeg", width=w, height=h,
                              provider_request_id=resposta.headers.get("x-request-id"), usd=self.custo(corpo),
                              ms=round((time.monotonic() - t0) * 1000), provider_seed=None, original=original,
                              original_mime="image/png")

    def _raise_for_status(self, resposta: httpx.Response) -> None:
        if resposta.status_code < 400:
            return
        texto = (resposta.text or "")[:300]
        if resposta.status_code == 400 and any(m in texto.lower() for m in _MARCAS_DE_RECUSA):
            raise GeracaoRecusada("O provedor de imagem recusou o pedido pelo filtro de conteúdo.")
        if resposta.status_code in (401, 403):
            raise GeracaoFalhou("Credencial recusada pela API de imagens.", kind="not_configured",
                                status=resposta.status_code)
        if resposta.status_code == 402:
            raise GeracaoFalhou("Sem crédito na API de imagens.", kind="billing", status=resposta.status_code)
        if resposta.status_code == 404:
            raise GeracaoFalhou(f"Modelo '{self.model}' ou rota não encontrada na API de imagens.",
                                kind="not_configured", status=resposta.status_code)
        if resposta.status_code == 429:
            raise GeracaoFalhou("Limite de requisições da API de imagens atingido.", retryable=True,
                                status=resposta.status_code)
        if resposta.status_code >= 500:
            raise GeracaoFalhou(f"Erro {resposta.status_code} da API de imagens.", retryable=True,
                                status=resposta.status_code)
        raise GeracaoFalhou(f"Pedido rejeitado pela API de imagens: {texto}", status=resposta.status_code)
