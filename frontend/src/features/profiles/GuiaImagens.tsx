/**
 * Guia "Imagens" (evolução 2, onda E1; ADR-042): a galeria da persona — geradas, enviadas e herdadas dos avatares
 * legados — com a principal (é ela que o avatar serve), o estado de cada uma, a proveniência e o custo. Gerar é
 * assíncrono: a rota responde 202 e cada imagem chega pelo evento `persona.image.updated`; a galeria se relê ao
 * receber o evento DESTA persona, sem polling.
 *
 * A identidade visual (`visual.*`) mora aqui, e não na guia Persona: ela não vai ao modelo que escreve, só à
 * receita das fotos.
 */
import { ImagePlus, Sparkles, Star, Trash2, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { PersonaImage } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { AutoGrid } from '../../components/Page';
import { isRecord } from '../../lib/format';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import type { Tone } from '../../lib/status';
import { onLiveEvent } from '../../store/live';
import { toast, toastError } from '../../store/toasts';
import { Carregando } from './detalheComum';
import { nomeDe, type Pessoa } from './pessoa';
import { SecaoEditavel, comoTexto, paraTexto, type CampoDef } from './SecaoEditavel';
import styles from './Profiles.module.css';
import { useAiStatus } from './useAiStatus';

const LIMITE_DE_UPLOAD = 10 * 1024 * 1024;
const TIPOS_DE_UPLOAD = ['image/jpeg', 'image/png'];
const EVENTO_IMAGEM = 'persona.image.updated';

const ESTADO: Record<string, { label: string; tone: Tone }> = {
  pending: { label: 'gerando…', tone: 'info' },
  failed: { label: 'falhou', tone: 'danger' },
  refused: { label: 'recusada pelo provedor', tone: 'warning' },
};

const ORIGEM: Record<string, string> = {
  generated: 'gerada',
  upload: 'enviada',
  imported_legacy: 'avatar antigo',
};

/** 29.81: a resposta "esta foto foi feita por IA?" no `<select>` (o valor vazio é "não informado"). */
const FEITA_POR_IA: { valor: '' | 'true' | 'false'; rotulo: string }[] = [
  { valor: '', rotulo: 'Não informado (sai sem rótulo de IA)' },
  { valor: 'true', rotulo: 'Sim, feita por IA (sai com rótulo de IA)' },
  { valor: 'false', rotulo: 'Não, é foto real (sai sem rótulo de IA)' },
];

/** A mesma resposta na própria foto, em rótulo curto: o cartão é estreito, e o selo ao lado já diz a consequência. */
const FEITA_POR_IA_CURTO: { valor: '' | 'true' | 'false'; rotulo: string }[] = [
  { valor: '', rotulo: 'Não informado' },
  { valor: 'true', rotulo: 'Sim, feita por IA' },
  { valor: 'false', rotulo: 'Não, foto real' },
];

function paraResposta(valor: string): boolean | null {
  return valor === 'true' ? true : valor === 'false' ? false : null;
}

function daResposta(v: boolean | null | undefined): '' | 'true' | 'false' {
  return v === true ? 'true' : v === false ? 'false' : '';
}

/** O rótulo de IA com que a foto sai numa publicação (29.79): gerada e importada sempre com; a enviada, pelo dono. */
export function saiComRotulo(i: Pick<PersonaImage, 'source' | 'feita_por_ia'>): boolean {
  return i.source !== 'upload' || i.feita_por_ia === true;
}

const TITULO_DO_ERRO: Record<string, string> = {
  image_not_configured: 'Gerador de imagem sem chave',
  persona_minor: 'Persona menor de idade',
  ai_budget: 'Teto de gasto de IA atingido',
};

const CAMPOS_VISUAIS: CampoDef[] = [
  { chave: 'appearance', rotulo: 'Aparência', tipo: 'longo', dica: 'Rosto, cabelo, corpo — como um fotógrafo descreveria.' },
  { chave: 'visual_style', rotulo: 'Estilo visual', tipo: 'longo', dica: 'Roupas e acessórios típicos.' },
  { chave: 'photo_scenario', rotulo: 'Cenário das fotos', tipo: 'longo', dica: 'Onde ela costuma ser fotografada.' },
  { chave: 'palette', rotulo: 'Paleta' },
  { chave: 'age_presentation', rotulo: 'Idade aparente' },
  { chave: 'gender_presentation', rotulo: 'Apresentação de gênero' },
];

export function AbaImagens({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const { ai, falhou: aiFalhou } = useAiStatus();
  const [imagens, setImagens] = useState<PersonaImage[] | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [quantas, setQuantas] = useState('1');
  const [gerando, setGerando] = useState(0);
  const [pedindo, setPedindo] = useState(false);
  const [erroGerar, setErroGerar] = useState<{ code: string; message: string } | null>(null);
  const [erroUpload, setErroUpload] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [feitaPorIa, setFeitaPorIa] = useState<'' | 'true' | 'false'>('');
  const [busy, setBusy] = useState<string | null>(null);
  const token = useRef(0);
  const nome = nomeDe(profile);

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    try {
      const r = await api.listPersonaImages(profile.id);
      if (meu !== token.current) return;
      setImagens(r);
      setErro(null);
    } catch (e) {
      if (meu === token.current) setErro(toLoadError(e));
    }
  }, [profile.id]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  // Cada imagem pronta, recusada ou com falha chega por evento; relê a galeria só quando é DESTA persona.
  useEffect(() => onLiveEvent((ev) => {
    if (ev.kind !== EVENTO_IMAGEM || !isRecord(ev.data) || ev.data.profile_id !== profile.id) return;
    const status = typeof ev.data.status === 'string' ? ev.data.status : '';
    if (['ready', 'failed', 'refused'].includes(status)) setGerando((n) => Math.max(0, n - 1));
    void carregar();
    // A principal é o avatar e a faixa da Visão geral: a persona se relê quando ela pode ter mudado.
    if (status === 'ready' || status === 'primary') void onChanged();
  }), [profile.id, carregar, onChanged]);

  const img = ai?.image ?? null;
  const n = Number(quantas);
  const pago = !!img && !img.simulated;
  const motivoGerar = img && !img.configured && !img.simulated
    ? 'O gerador de imagem não tem chave configurada.' : null;

  async function gerar() {
    if (motivoGerar || pedindo) return;
    setPedindo(true);
    setErroGerar(null);
    try {
      const r = await api.generatePersonaImages(profile.id, n);
      setGerando((g) => g + r.count);
      toast({ tone: 'info', title: `Gerando ${r.count} imagem(ns)…`,
              message: r.simulated ? 'Gerador simulado: sem custo.' : `Pelo provedor ${r.provider}; cada uma aparece aqui quando ficar pronta.` });
      await carregar();
    } catch (e) {
      const x = toApiError(e);
      setErroGerar({ code: x.code, message: x.message });
    } finally {
      setPedindo(false);
    }
  }

  async function enviar(arquivo: File | undefined) {
    if (!arquivo) return;
    setErroUpload(null);
    if (!TIPOS_DE_UPLOAD.includes(arquivo.type)) {
      setErroUpload('Envie uma foto JPEG ou PNG.');
      return;
    }
    if (arquivo.size > LIMITE_DE_UPLOAD) {
      setErroUpload('A foto passa de 10 MB.');
      return;
    }
    setEnviando(true);
    try {
      await api.uploadPersonaImage(profile.id, arquivo, paraResposta(feitaPorIa));
      toast({ tone: 'success', title: 'Foto enviada',
              message: feitaPorIa === 'true' ? 'Ela sai com o rótulo de IA nas publicações.' : 'Ela sai sem o rótulo de IA nas publicações.' });
      await carregar();
      await onChanged();
    } catch (e) {
      setErroUpload(toApiError(e).message);
    } finally {
      setEnviando(false);
    }
  }

  async function tornarPrincipal(i: PersonaImage) {
    setBusy(i.id);
    try {
      await api.setPrimaryPersonaImage(profile.id, i.id);
      toast({ tone: 'success', title: 'Foto principal trocada', message: 'É ela que aparece como avatar da persona.' });
      await carregar();
      await onChanged();
    } catch (e) {
      toastError('Não foi possível trocar a foto principal', e);
    } finally {
      setBusy(null);
    }
  }

  async function corrigirFeitaPorIa(i: PersonaImage, valor: string) {
    setBusy(i.id);
    try {
      await api.setPersonaImageFeitaPorIa(profile.id, i.id, paraResposta(valor));
      toast({ tone: 'success', title: valor === 'true' ? 'Marcada como feita por IA' : 'Marcada sem rótulo de IA',
              message: 'As publicações ainda por fazer com esta foto seguem a resposta nova; o que você aprovou antes '
                       + 'para elas volta a pedir o seu aval.' });
      await carregar();
    } catch (e) {
      toastError('Não foi possível corrigir a foto', e);
    } finally {
      setBusy(null);
    }
  }

  async function apagar(i: PersonaImage) {
    if (!(await confirm({ title: 'Apagar esta foto?', body: 'O registro e os arquivos saem do armazenamento.',
                          confirmLabel: 'Apagar', danger: true })).confirmed) return;
    setBusy(i.id);
    try {
      await api.deletePersonaImage(profile.id, i.id);
      await carregar();
      await onChanged();
    } catch (e) {
      toastError('Não foi possível apagar a foto', e);
    } finally {
      setBusy(null);
    }
  }

  async function salvarVisual(v: Record<string, string>) {
    try {
      await api.updatePersona(profile.id, {
        visual: Object.fromEntries(CAMPOS_VISUAIS.map((c) => [c.chave, paraTexto(v[c.chave])])),
      });
      toast({ tone: 'success', title: 'Identidade visual salva', message: 'As próximas fotos saem desta receita.' });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível salvar a identidade visual', e);
    }
  }

  const visual = (profile.visual ?? {}) as Record<string, unknown>;

  return (
    <div className={styles.stack}>
      <Card>
        <CardHeader title="Imagens" subtitle="A principal é o avatar da persona. O nome nunca entra no pedido de imagem." />
        <CardBody className={styles.stack}>
          <div className={styles.imageActions}>
            <Field label="Quantas gerar">
              {({ id }) => (
                <Select id={id} value={quantas} onChange={(e) => setQuantas(e.target.value)}>
                  <option value="1">1</option>
                  <option value="2">2</option>
                  <option value="3">3</option>
                </Select>
              )}
            </Field>
            <Button icon={Sparkles} variant="primary" loading={pedindo} disabledReason={motivoGerar}
                    onClick={() => void gerar()}>
              Gerar mais
            </Button>
            <Field label="Esta foto foi feita por IA?"
                   hint="Foto realista feita por IA sai com o rótulo de IA do Instagram. Dá para corrigir depois, na foto.">
              {({ id, describedBy }) => (
                <Select id={id} aria-describedby={describedBy} value={feitaPorIa} disabled={enviando}
                        onChange={(e) => setFeitaPorIa(e.target.value as '' | 'true' | 'false')}>
                  {FEITA_POR_IA.map((o) => <option key={o.valor} value={o.valor}>{o.rotulo}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Enviar foto" hint="JPEG ou PNG, até 10 MB.">
              {({ id, describedBy }) => (
                <input id={id} aria-describedby={describedBy} type="file" accept="image/jpeg,image/png"
                       disabled={enviando} className={styles.fileInput}
                       onChange={(e) => { void enviar(e.target.files?.[0]); e.target.value = ''; }} />
              )}
            </Field>
          </div>
          <p className={styles.detail}>
            {img ? (
              <>
                Gerador: <strong>{img.provider}</strong> · {img.model} · qualidade {img.quality}.{' '}
                {img.simulated ? 'Simulado: sem custo e nada sai desta máquina.'
                  : img.price_per_image_usd != null
                    ? `Chamada paga: ≈ US$ ${(img.price_per_image_usd * n).toFixed(3)} por ${n} imagem(ns).`
                    : 'Chamada paga (preço não declarado).'}
                {pago && img.sends_data_externally
                  ? ' Saem desta máquina os atributos da persona (nunca o nome) e, da segunda foto em diante, a principal como referência.'
                  : ''}
              </>
            ) : aiFalhou ? 'Não foi possível ler o gerador de imagem agora: conte com custo se ele for pago.' : 'Lendo o gerador de imagem…'}
          </p>
          {gerando > 0 ? (
            <Banner tone="info" icon={Sparkles} role="status" compact>
              Gerando {gerando} imagem(ns)… cada uma aparece aqui quando ficar pronta.
            </Banner>
          ) : null}
          {erroGerar ? (
            <Banner tone="danger" icon={TriangleAlert} role="alert" title={TITULO_DO_ERRO[erroGerar.code] ?? 'Não foi possível gerar'}>
              {erroGerar.message}
            </Banner>
          ) : null}
          {erroUpload ? (
            <Banner tone="danger" icon={TriangleAlert} role="alert" title="Foto não enviada">{erroUpload}</Banner>
          ) : null}

          {imagens === null ? (
            erro ? <LoadErrorState what="as imagens" error={erro} onRetry={() => void carregar()} compact /> : <Carregando />
          ) : imagens.length === 0 ? (
            <EmptyState icon={ImagePlus} title="Nenhuma foto ainda" compact
                        hint="Gere pela receita da identidade visual (abaixo) ou envie uma foto.">
              {nome} ainda não tem fotos.
            </EmptyState>
          ) : (
            <AutoGrid min="200px">
              {imagens.map((i, k) => (
                <Foto key={i.id} imagem={i} alt={`Foto ${k + 1} de ${nome}`} busy={busy === i.id}
                      onPrincipal={() => void tornarPrincipal(i)} onApagar={() => void apagar(i)}
                      onFeitaPorIa={(v) => void corrigirFeitaPorIa(i, v)} />
              ))}
            </AutoGrid>
          )}
        </CardBody>
      </Card>

      <SecaoEditavel
        titulo="Identidade visual"
        subtitulo="A receita das fotos: fica guardada e NÃO vai ao modelo que escreve."
        campos={CAMPOS_VISUAIS}
        iniciais={Object.fromEntries(CAMPOS_VISUAIS.map((c) => [c.chave, comoTexto(visual[c.chave])]))}
        onSalvar={salvarVisual}
      />
    </div>
  );
}

function Foto({ imagem: i, alt, busy, onPrincipal, onApagar, onFeitaPorIa }: {
  imagem: PersonaImage;
  alt: string;
  busy: boolean;
  onPrincipal: () => void;
  onApagar: () => void;
  onFeitaPorIa: (valor: string) => void;
}) {
  const estado = ESTADO[i.status];
  const rotulo = saiComRotulo(i);
  return (
    <figure className={styles.imageCard} data-primary={i.is_primary || undefined}>
      {i.status === 'ready' ? (
        <img className={styles.imageMedia} src={i.url} alt={alt} loading="lazy" />
      ) : (
        <div className={styles.imagePlaceholder}>{estado?.label ?? i.status}</div>
      )}
      <figcaption className={styles.imageCaption}>
        <div className={styles.accountBadges}>
          {i.is_primary ? <Badge size="sm" tone="accent" icon={Star}>principal</Badge> : null}
          {estado ? <Badge size="sm" tone={estado.tone}>{estado.label}</Badge> : null}
          {i.provider === 'simulated' ? <Badge size="sm" tone="warning">simulado</Badge> : null}
          <Badge size="sm" tone="neutral">{ORIGEM[i.source] ?? i.source}</Badge>
          {/* 29.81: o upload sem resposta sai sem rótulo, mas não é "foto real": ninguém disse. */}
          {rotulo ? <Badge size="sm" tone="info">com rótulo de IA</Badge>
            : i.feita_por_ia === false ? <Badge size="sm" tone="neutral">sem rótulo de IA (foto real)</Badge>
            : <Badge size="sm" tone="warning">rótulo de IA não informado</Badge>}
        </div>
        {i.source === 'upload' ? (
          <Field label="Feita por IA?">
            {({ id }) => (
              <Select id={id} value={daResposta(i.feita_por_ia)} disabled={busy}
                      onChange={(e) => onFeitaPorIa(e.target.value)}>
                {FEITA_POR_IA_CURTO.map((o) => <option key={o.valor} value={o.valor}>{o.rotulo}</option>)}
              </Select>
            )}
          </Field>
        ) : null}
        {i.provider || i.model ? (
          <span className={styles.muted}>{[i.provider, i.model].filter(Boolean).join(' · ')}</span>
        ) : null}
        {i.seed != null || i.aspect ? (
          <span className={styles.muted}>receita: {[i.seed != null ? `semente ${i.seed}` : null, i.aspect].filter(Boolean).join(' · ')}</span>
        ) : null}
        <span className={styles.muted}>{i.cost_usd > 0 ? `custo US$ ${i.cost_usd.toFixed(3)}` : 'sem custo'}</span>
        {i.error ? <span className={styles.imageError}>{i.error}</span> : null}
        <div className={styles.imageButtons}>
          {i.status === 'ready' && !i.is_primary ? (
            <Button size="sm" variant="ghost" icon={Star} loading={busy} onClick={onPrincipal}>Tornar principal</Button>
          ) : null}
          <Button size="sm" variant="dangerGhost" icon={Trash2} loading={busy} onClick={onApagar}>Apagar</Button>
        </div>
      </figcaption>
    </figure>
  );
}
