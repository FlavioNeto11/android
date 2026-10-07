"""A receita de uma foto da persona (`PersonaImageSpec`): identidade fixa + eixos sorteados por semente.

Só biblioteca padrão: quem gera a imagem (simulado com Pillow, OpenAI por HTTP) mora nos adaptadores; aqui fica o
que os dois recebem igual — o que da pessoa entra na foto, como a semente nasce, quais eixos variam e o prompt
determinístico em inglês. O nome da persona NUNCA entra no prompt: só atributos. Abaixo da maioridade não há receita: `MenorDeIdade`.

A semente é `sha256(f"{persona_id}:{indice}:{SPEC_VERSION}")[:4] & 0x7FFFFFFF` (31 bits: cabe em INTEGER dos dois
bancos). Mesma pessoa, mesmo índice, mesma versão → a mesma receita, sempre; mudar `SPEC_VERSION` muda tudo de
propósito. A imagem 0 é a PRINCIPAL: busto, rosto visível, 1:1.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field

from app.modules.identity.domain.persona import MAIORIDADE

SPEC_VERSION = 1
#: Tamanho que se pede ao provedor por proporção (o OpenAI gera 1024×1024 ou 1024×1536); o recorte final é do pós.
TAMANHO_DO_PROVEDOR: dict[str, tuple[int, int]] = {"1:1": (1024, 1024), "4:5": (1024, 1536), "3:4": (1024, 1536),
                                                   "9:16": (1024, 1536)}
#: Proporção → dimensões finais do JPEG servido (largura, altura), como o Instagram publica.
TAMANHO_FINAL: dict[str, tuple[int, int]] = {"1:1": (1080, 1080), "4:5": (1080, 1350), "3:4": (1080, 1440),
                                             "9:16": (1080, 1920)}

CAMERAS = ("front smartphone camera selfie", "recent smartphone rear camera", "older smartphone camera",
           "compact digital camera", "amateur DSLR")
EPOCAS = ("taken recently", "taken two or three years ago", "taken around 2012")
LUZES = ("soft window light", "late afternoon golden light", "harsh night flash", "fluorescent indoor light",
         "overcast daylight")
AMBIENTES_PADRAO = ("living room", "city street", "park", "neighborhood café", "kitchen", "balcony with plants")
ENQUADRAMENTOS = ("head and shoulders", "waist-up", "full body", "arm's length selfie", "close-up portrait")
POSES = ("relaxed smile looking at the camera", "candid, looking away", "laughing mid-sentence",
         "neutral expression", "in the middle of an activity")
PRODUCOES = ("casual, no makeup, unposed", "put together, light makeup", "polished, styled")
PROPORCOES = ("1:1", "4:5", "3:4", "9:16")


class MenorDeIdade(ValueError):
    """Persona com menos de `MAIORIDADE` anos não tem foto — nem simulada."""


class GeracaoRecusada(RuntimeError):
    """O provedor recusou pelo filtro de conteúdo. Vira `status='refused'`; nunca cai para o simulado."""


class GeracaoFalhou(RuntimeError):
    """Falha de provedor (rede, credencial, cota). `kind` como em `AIError`: error | not_configured | billing."""

    def __init__(self, message: str, *, kind: str = "error", retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status


class OrcamentoEsgotado(RuntimeError):
    """O teto diário de IA em US$ já foi atingido: nenhuma imagem paga sai."""


@dataclass(frozen=True, slots=True)
class PersonaIdentity:
    """O que da pessoa entra na receita — SEM nome. `initials` só pinta o carimbo do simulado; nunca vai ao prompt."""

    appearance: str | None = None
    visual_style: str | None = None
    photo_scenario: str | None = None
    interests: tuple[str, ...] = ()
    age: int | None = None
    gender: str | None = None
    profession: str | None = None
    city: str | None = None
    palette: str | None = None
    initials: str = ""


@dataclass(frozen=True, slots=True)
class PersonaImageSpec:
    spec_version: int
    persona_id: str
    index: int
    seed: int
    identity: PersonaIdentity
    camera: str
    era: str
    light: str
    setting: str
    framing: str
    pose: str
    production: str
    aspect: str
    #: Pós-processamento (variação): JPEG 55–92, ruído leve, desfoque leve, redução e deslocamento do recorte.
    jpeg_quality: int
    noise_sigma: float
    blur_radius: float
    downscale: float
    crop_shift: float
    prompt: str = field(default="")

    @property
    def provider_size(self) -> tuple[int, int]:
        return TAMANHO_DO_PROVEDOR[self.aspect]

    @property
    def final_size(self) -> tuple[int, int]:
        return TAMANHO_FINAL[self.aspect]

    @property
    def prompt_sha256(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, texto: str) -> PersonaImageSpec:
        dados = json.loads(texto)
        identidade = dados.pop("identity")
        identidade["interests"] = tuple(identidade.get("interests") or ())
        return cls(identity=PersonaIdentity(**identidade), **dados)


def semente(persona_id: str, indice: int, version: int = SPEC_VERSION) -> int:
    digest = hashlib.sha256(f"{persona_id}:{indice}:{version}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def montar_spec(identity: PersonaIdentity, persona_id: str, indice: int) -> PersonaImageSpec:
    """A receita do índice `indice` desta pessoa. Determinística; a 0 é a principal (busto, rosto, 1:1)."""
    if identity.age is not None and identity.age < MAIORIDADE:
        raise MenorDeIdade(f"persona com {identity.age} anos: só se fotografa pessoa com {MAIORIDADE} ou mais")
    seed = semente(persona_id, indice)
    rnd = random.Random(seed)
    ambientes = tuple(a for a in (identity.photo_scenario,) if a) or tuple(identity.interests[:3]) or AMBIENTES_PADRAO
    principal = indice == 0
    spec = PersonaImageSpec(
        spec_version=SPEC_VERSION, persona_id=persona_id, index=indice, seed=seed, identity=identity,
        camera=rnd.choice(CAMERAS), era=rnd.choice(EPOCAS), light=rnd.choice(LUZES), setting=rnd.choice(ambientes),
        framing="head and shoulders" if principal else rnd.choice(ENQUADRAMENTOS),
        pose="relaxed, face clearly visible, looking at the camera" if principal else rnd.choice(POSES),
        production=rnd.choice(PRODUCOES), aspect="1:1" if principal else rnd.choice(PROPORCOES),
        jpeg_quality=rnd.randint(55, 92), noise_sigma=round(rnd.uniform(0.0, 4.0), 2),
        blur_radius=round(rnd.uniform(0.0, 0.8), 2), downscale=round(rnd.uniform(0.6, 1.0), 2),
        crop_shift=round(rnd.uniform(-0.1, 0.1), 3))
    return PersonaImageSpec(**{**asdict(spec), "identity": identity, "prompt": prompt_de(spec)})


def prompt_de(spec: PersonaImageSpec) -> str:
    """Inglês, determinístico, sem o nome da persona. Tudo que é texto da pessoa é atributo."""
    p = spec.identity
    quem = {"feminino": "woman", "masculino": "man"}.get((p.gender or "").lower(), "person")
    idade = f"{p.age}-year-old " if p.age is not None else ""
    partes = [f"Candid amateur photo of a {idade}adult {quem}"]
    if p.profession:
        partes[0] += f" who works as a {_limpo(p.profession)}"
    if p.appearance:
        partes.append(_limpo(p.appearance))
    if p.visual_style:
        partes.append(f"style: {_limpo(p.visual_style)}")
    if p.palette:
        partes.append(f"color palette: {_limpo(p.palette)}")
    onde = _limpo(spec.setting) + (f" in {_limpo(p.city)}" if p.city else "")
    partes.append(f"setting: {onde}")
    partes.append(f"{spec.framing}, {spec.pose}")
    partes.append(f"{spec.camera}, {spec.light}, {spec.era}, {spec.production}")
    partes.append("realistic, unretouched, natural skin, no text, no logo, no watermark, no caption")
    return ". ".join(partes) + "."


def _limpo(texto: str) -> str:
    """Atributo de persona vira parte do prompt: sem quebra de linha nem sinais de marcação, e curto."""
    return " ".join(texto.replace("<", " ").replace(">", " ").split())[:240].rstrip(".")


@dataclass(frozen=True, slots=True)
class PersonaImageRecord:
    """Uma linha de `persona_images` (048), como a aplicação a vê."""

    id: str
    persona_id: str
    status: str
    source: str
    is_primary: bool
    storage: str = "disk"
    storage_key: str | None = None
    original_key: str | None = None
    spec_json: str = "{}"
    prompt_sha256: str | None = None
    provider: str | None = None
    model: str | None = None
    seed: int = 0
    provider_seed: str | None = None
    provider_request_id: str | None = None
    width: int | None = None
    height: int | None = None
    bytes_sha256: str | None = None
    cost_usd: float = 0.0
    error: str | None = None
    created_at: str = ""
    #: 29.81: o dono disse se o upload foi feito por IA (`None` = não informado). Só o upload usa.
    feita_por_ia: bool | None = None

    @property
    def aspect(self) -> str | None:
        try:
            valor = json.loads(self.spec_json).get("aspect")
        except (ValueError, AttributeError):
            return None
        return valor if isinstance(valor, str) else None
