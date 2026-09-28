"""O serviço de imagens da persona: gera N pela receita, guarda no storage, registra custo, define a principal.

Nível de aplicação (D2/D3): só portas (`ports.py`) e domínio. Quem liga banco, storage, provedor e eventos é
`infrastructure/persona_images.py`; quem expõe rotas é `api.py`. O que se garante aqui, e não nos adaptadores:

- menor de idade não tem foto (`montar_spec` recusa);
- o teto DIÁRIO de IA em US$ é conferido ANTES de pedir uma imagem paga (o simulado não gasta e não confere);
- a primeira imagem pronta de uma pessoa sem principal vira a principal; as seguintes recebem a principal como
  REFERÊNCIA, para o rosto se manter — é o único dado além de atributos que sai da máquina;
- recusa do filtro vira `refused` e falha vira `failed`, com a linha no banco e o custo (quando houve) no relatório;
  nunca se cai do pago para o simulado;
- o original do provedor é guardado intacto ao lado do JPEG servido (`original_key`): a recompressão apaga a
  proveniência, e a variação honesta é do arquivo servido, não do registro.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable

from app.modules.identity.application.ports import (EventSink, GeneratedImage, ImageAccounting, ImageBlobStore,
                                                     ImageGenerator, PersonaImageRepository)
from app.modules.identity.domain.persona_image import (GeracaoFalhou, GeracaoRecusada, OrcamentoEsgotado,
                                                       PersonaIdentity, PersonaImageRecord, montar_spec)

#: Evento que o painel escuta para atualizar a galeria: uma linha mudou de estado (pronta, recusada, falhou, apagada).
EVENTO_IMAGEM = "persona.image.updated"
#: Chave legada dos avatares que existiam antes da 048 (`data/avatars/<profile_id>.jpg`).
CHAVE_LEGADA = "avatars/{persona_id}.jpg"
#: Quantas imagens uma chamada da API pode pedir de uma vez.
MAX_POR_PEDIDO = 3


class PersonaImageService:
    def __init__(self, *, generator: ImageGenerator, records: PersonaImageRepository, blobs: ImageBlobStore,
                 accounting: ImageAccounting, events: EventSink, new_id: Callable[[], str],
                 now_iso: Callable[[], str], measure: Callable[[bytes], tuple[int, int] | None],
                 per_persona: int = 1, on_create: bool = True) -> None:
        self.generator = generator
        self.records = records
        self.blobs = blobs
        self.accounting = accounting
        self.events = events
        self._new_id = new_id
        self._now = now_iso
        self._measure = measure
        self.per_persona = max(0, min(per_persona, MAX_POR_PEDIDO))
        self.on_create = on_create

    # ------------------------------------------------------------------ leitura
    def listar(self, persona_id: str) -> list[PersonaImageRecord]:
        return self.records.list(persona_id)

    def obter(self, persona_id: str, image_id: str) -> PersonaImageRecord | None:
        return self.records.get(persona_id, image_id)

    def principal(self, persona_id: str) -> PersonaImageRecord | None:
        return next((r for r in self.records.list(persona_id) if r.is_primary and r.status == "ready"), None)

    def bytes_de(self, record: PersonaImageRecord) -> bytes | None:
        return self.blobs.get(record.storage_key) if record.storage_key else None

    # ------------------------------------------------------------------ geração
    def conferir_orcamento(self) -> None:
        """Teto do dia (Configuração › Limites) conferido antes de gastar. O simulado não custa e não bloqueia."""
        if self.generator.simulated:
            return
        limite = self.accounting.daily_limit_usd()
        if limite > 0 and (gasto := self.accounting.spent_today_usd()) >= limite:
            raise OrcamentoEsgotado(f"Teto de gasto de IA do dia atingido: US$ {gasto:.2f} de US$ {limite:.2f}. "
                                    "Ajuste o limite em Configuração › Limites para gerar imagens.")

    async def gerar(self, persona_id: str, identity: PersonaIdentity, *, count: int) -> list[PersonaImageRecord]:
        """`count` imagens novas para esta pessoa, uma a uma. Cada índice novo continua a numeração já existente:
        a receita de índice `k` é sempre a mesma para a mesma pessoa, então repetir um pedido não repete uma foto."""
        count = max(1, min(count, MAX_POR_PEDIDO))
        self.conferir_orcamento()
        saida: list[PersonaImageRecord] = []
        for _ in range(count):
            existentes = self.records.list(persona_id)
            indice = len(existentes)
            principal = self.principal(persona_id)
            referencia = self.bytes_de(principal) if principal is not None else None
            spec = montar_spec(identity, persona_id, indice)
            image_id = self._new_id()
            pendente = PersonaImageRecord(
                id=image_id, persona_id=persona_id, status="pending", source="generated", is_primary=False,
                spec_json=spec.to_json(), prompt_sha256=spec.prompt_sha256, provider=self.generator.name,
                model=self.generator.model, seed=spec.seed, created_at=self._now())
            self.records.insert(pendente)
            try:
                imagem = await self.generator.generate(spec, reference=referencia)
            except GeracaoRecusada as exc:
                registro = self._encerrar(pendente, status="refused", erro=str(exc), imagem=None)
            except GeracaoFalhou as exc:
                registro = self._encerrar(pendente, status="failed", erro=str(exc), imagem=None)
            else:
                registro = await self._guardar(pendente, imagem, tornar_principal=principal is None)
            saida.append(registro)
            if registro.status != "ready":
                break                              # recusa ou falha: não insiste nas seguintes desta leva
        return saida

    async def _guardar(self, pendente: PersonaImageRecord, imagem: GeneratedImage, *,
                       tornar_principal: bool) -> PersonaImageRecord:
        chave = f"personas/{pendente.persona_id}/{pendente.id}.jpg"
        await self.blobs.put(chave, imagem.data, content_type=imagem.mime)
        original: str | None = None
        if imagem.original is not None:
            extensao = "png" if (imagem.original_mime or "").endswith("png") else "bin"
            original = f"personas/{pendente.persona_id}/{pendente.id}.orig.{extensao}"
            await self.blobs.put(original, imagem.original, content_type=imagem.original_mime or "application/octet-stream")
        self.records.finish(pendente.persona_id, pendente.id, status="ready", storage_key=chave, original_key=original,
                            width=imagem.width, height=imagem.height,
                            bytes_sha256=hashlib.sha256(imagem.data).hexdigest(), cost_usd=imagem.usd,
                            provider_request_id=imagem.provider_request_id, provider_seed=imagem.provider_seed,
                            error=None)
        self.accounting.record(provider=self.generator.name, model=self.generator.model, usd=imagem.usd, ms=imagem.ms,
                               ok=True, error=None)
        if tornar_principal:
            self.records.set_primary(pendente.persona_id, pendente.id)
        pronto = self.records.get(pendente.persona_id, pendente.id)
        self._anunciar(pendente.persona_id, pendente.id, "ready")
        assert pronto is not None
        return pronto

    def _encerrar(self, pendente: PersonaImageRecord, *, status: str, erro: str,
                  imagem: GeneratedImage | None) -> PersonaImageRecord:
        del imagem
        self.records.finish(pendente.persona_id, pendente.id, status=status, storage_key=None, original_key=None,
                            width=None, height=None, bytes_sha256=None, cost_usd=0.0, provider_request_id=None,
                            provider_seed=None, error=erro[:500])
        self.accounting.record(provider=self.generator.name, model=self.generator.model, usd=0.0, ms=0, ok=False,
                               error=f"{status}: {erro[:200]}")
        registro = self.records.get(pendente.persona_id, pendente.id)
        self._anunciar(pendente.persona_id, pendente.id, status, level="warn")
        assert registro is not None
        return registro

    # ------------------------------------------------------------------ upload, principal, apagar, legado
    async def registrar_upload(self, persona_id: str, data: bytes, mime: str) -> PersonaImageRecord:
        """Foto enviada pela pessoa: guardada como veio (sem pós-processamento), principal se for a primeira."""
        image_id = self._new_id()
        extensao = "png" if mime.endswith("png") else "jpg"
        chave = f"personas/{persona_id}/{image_id}.{extensao}"
        await self.blobs.put(chave, data, content_type=mime)
        medidas = self._measure(data)
        registro = PersonaImageRecord(
            id=image_id, persona_id=persona_id, status="ready", source="upload", is_primary=False, storage_key=chave,
            provider="upload", width=medidas[0] if medidas else None, height=medidas[1] if medidas else None,
            bytes_sha256=hashlib.sha256(data).hexdigest(), created_at=self._now())
        self.records.insert(registro)
        if self.principal(persona_id) is None:
            self.records.set_primary(persona_id, image_id)
        self._anunciar(persona_id, image_id, "ready")
        pronto = self.records.get(persona_id, image_id)
        assert pronto is not None
        return pronto

    def definir_principal(self, persona_id: str, image_id: str) -> PersonaImageRecord:
        registro = self.records.get(persona_id, image_id)
        if registro is None:
            raise KeyError(image_id)
        if registro.status != "ready":
            raise ValueError("só uma imagem pronta pode ser a principal")
        self.records.set_primary(persona_id, image_id)
        self._anunciar(persona_id, image_id, "primary")
        pronto = self.records.get(persona_id, image_id)
        assert pronto is not None
        return pronto

    def apagar(self, persona_id: str, image_id: str) -> None:
        """Apaga o registro e os arquivos (servido e original). A chave legada dos avatares não é apagada: é o
        arquivo antigo, e some só com decisão de pessoa."""
        registro = self.records.get(persona_id, image_id)
        if registro is None:
            raise KeyError(image_id)
        for chave in (registro.storage_key, registro.original_key):
            if chave and registro.source != "imported_legacy":
                self.blobs.delete(chave)
        self.records.delete(persona_id, image_id)
        self._anunciar(persona_id, image_id, "deleted")

    def importar_legado(self, persona_id: str) -> PersonaImageRecord | None:
        """Passo de partida idempotente: pessoa SEM imagem nenhuma + `avatars/<id>.jpg` no storage → linha
        `imported_legacy`, principal. Roda a cada partida e não faz nada na segunda vez."""
        if self.records.list(persona_id):
            return None
        chave = CHAVE_LEGADA.format(persona_id=persona_id)
        if not self.blobs.exists(chave):
            return None
        dados = self.blobs.get(chave) or b""
        medidas = self._measure(dados) if dados else None
        registro = PersonaImageRecord(
            id=self._new_id(), persona_id=persona_id, status="ready", source="imported_legacy", is_primary=False,
            storage_key=chave, provider="legacy", width=medidas[0] if medidas else None,
            height=medidas[1] if medidas else None, bytes_sha256=hashlib.sha256(dados).hexdigest() if dados else None,
            created_at=self._now())
        self.records.insert(registro)
        self.records.set_primary(persona_id, registro.id)
        return self.records.get(persona_id, registro.id)

    def _anunciar(self, persona_id: str, image_id: str, status: str, *, level: str = "info") -> None:
        self.events.emit(EVENTO_IMAGEM, f"imagem {image_id} da persona {persona_id}: {status}", level=level,
                         data={"profile_id": persona_id, "image_id": image_id, "status": status})
