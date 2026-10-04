import { CircleCheck, Download, Eye, EyeOff, FileQuestion, FileText, ImageIcon, ImageOff, Paperclip, ScanText } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, canalAnexoConteudoUrl, toApiError } from '../../api/client';
import type { CanalAnexo, CanalAnexosPagina } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Checkbox, Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import { tempoRelativo, useNow } from '../../lib/time';
import { AnexarAoCartao } from './AnexarAoCartao';
import {
  CANAIS, desdeDoPeriodo, origemDoAnexo, PERIODOS, semPrevia, tamanhoLegivel, tipoDoAnexo, type PeriodoId, type Tipo,
} from './anexos';
import styles from './Canais.module.css';

/** Cada miniatura é o arquivo inteiro (a rota não tem versão reduzida): páginas curtas para não puxar dezenas de MB de uma vez. */
const PAGINA = 12;

const ROTULO_DO_TIPO: Record<Tipo, string> = { imagem: 'Imagem', pdf: 'PDF', outro: 'Arquivo' };

interface Filtros {
  canal: string;
  direcao: string;
  periodo: PeriodoId;
  soDono: boolean;
}
const SEM_FILTROS: Filtros = { canal: '', direcao: '', periodo: 'tudo', soDono: false };
const temFiltro = (f: Filtros) => f.canal !== '' || f.direcao !== '' || f.periodo !== 'tudo' || f.soDono;

type ObterArquivo = (id: number) => Promise<string>;

/**
 * F5: o arquivo de cada imagem baixa UMA vez enquanto a aba está aberta e fica em memória (URL `blob:`). A rota responde
 * `no-store` de propósito (a foto do dono não vai ao cache em disco do navegador); sem isto, cada troca de filtro e cada
 * prévia baixavam o arquivo inteiro de novo. Ao sair da aba, as URLs são revogadas.
 */
function useArquivosEmMemoria(): ObterArquivo {
  const cache = useRef(new Map<number, Promise<string>>());
  useEffect(() => {
    const mapa = cache.current;
    return () => {
      for (const p of mapa.values()) p.then((url) => URL.revokeObjectURL(url), () => undefined);
      mapa.clear();
    };
  }, []);
  return useCallback((id: number) => {
    let p = cache.current.get(id);
    if (!p) {
      p = api.canaisAnexoArquivo(id).then((b) => URL.createObjectURL(b));
      cache.current.set(id, p);
      p.catch(() => { cache.current.delete(id); });          // a falha não fica guardada: a próxima vez tenta de novo
    }
    return p;
  }, []);
}

function useUrlDoArquivo(obter: ObterArquivo, id: number, ativo: boolean): { url: string | null; falhou: boolean } {
  const [estado, setEstado] = useState<{ url: string | null; falhou: boolean }>({ url: null, falhou: false });
  useEffect(() => {
    if (!ativo) return undefined;
    let vivo = true;
    obter(id).then((url) => { if (vivo) setEstado({ url, falhou: false }); },
                   () => { if (vivo) setEstado({ url: null, falhou: true }); });
    return () => { vivo = false; };
  }, [obter, id, ativo]);
  return estado;
}

/**
 * A aba Anexos da tela Canais (item 28.24, fatias 4 e 5): o que passou pelos canais como arquivo, do mais novo ao mais velho.
 * Imagem e PDF têm prévia ao clicar; o arquivo que o dono mandou pode ir a um cartão do Trello, e a imagem dele pode ser
 * descrita pela IA (chamada paga, sempre com confirmação na linha). Esta tela não mostra nome de remetente: o produto não
 * guarda esse dado.
 */
export function AnexosTab() {
  const [filtros, setFiltros] = useState<Filtros>(SEM_FILTROS);
  const [pagina, setPagina] = useState<CanalAnexosPagina | null>(null);
  const [itens, setItens] = useState<CanalAnexo[]>([]);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregandoMais, setCarregandoMais] = useState(false);
  const [aberto, setAberto] = useState<number | null>(null);
  const [anexando, setAnexando] = useState<number | null>(null);
  /** O anexo que foi ao cartão, e se ele já estava lá (a rota não anexa duas vezes). */
  const [anexados, setAnexados] = useState<ReadonlyMap<number, boolean>>(new Map());
  const token = useRef(0);
  /** O "desde" do período é fixado na primeira página: "Carregar mais" pede a mesma janela, e o total não anda sozinho. */
  const desde = useRef<string | undefined>(undefined);
  const agora = useNow();
  const obterArquivo = useArquivosEmMemoria();

  const consulta = useCallback((f: Filtros, offset: number) => api.canaisAnexos({
    canal: f.canal || undefined,
    direcao: f.direcao || undefined,
    do_dono: f.soDono ? 'true' : undefined,
    desde: desde.current,
    limit: PAGINA,
    offset,
  }), []);

  // Um filtro novo começa do zero: a página antiga some (o `token` descarta a resposta que chegar atrasada).
  useEffect(() => {
    const meu = ++token.current;
    setPagina(null);
    setItens([]);
    setErro(null);
    setAberto(null);
    setAnexando(null);
    desde.current = desdeDoPeriodo(filtros.periodo, Date.now());
    consulta(filtros, 0).then((r) => {
      if (meu !== token.current) return;
      setPagina(r);
      setItens(r.items);
    }).catch((e: unknown) => {
      if (meu === token.current) setErro(toLoadError(e));
    });
    return () => { token.current += 1; };
  }, [filtros, consulta]);

  const tentarDeNovo = () => setFiltros((f) => ({ ...f }));

  const carregarMais = async () => {
    const meu = token.current;
    setCarregandoMais(true);
    setErro(null);
    try {
      const r = await consulta(filtros, itens.length);
      if (meu !== token.current) return;
      setPagina(r);
      setItens((atual) => [...atual, ...r.items.filter((n) => !atual.some((a) => a.id === n.id))]);
    } catch (e) {
      if (meu === token.current) setErro(toLoadError(e));
    } finally {
      if (meu === token.current) setCarregandoMais(false);
    }
  };

  const mudar = (parcial: Partial<Filtros>) => setFiltros((f) => ({ ...f, ...parcial }));
  const total = pagina?.total ?? 0;

  let corpo;
  if (pagina) {
    corpo = itens.length === 0 ? (
      temFiltro(filtros) ? (
        <EmptyState icon={Paperclip} title="Nenhum anexo com esses filtros"
                    hint="Tire um filtro para ver o resto."
                    actions={<Button variant="outline" onClick={() => setFiltros(SEM_FILTROS)}>Limpar filtros</Button>}>
          Nada passou pelos canais com essa combinação.
        </EmptyState>
      ) : (
        <EmptyState icon={Paperclip} title="Nenhum anexo ainda"
                    hint="Mande uma foto ou um PDF ao bot do Telegram e ele aparece aqui.">
          Os arquivos que você manda aos canais, e os que a Central envia, ficam listados nesta aba. Com um arquivo seu, dá
          para ver a prévia, anexá-lo a um cartão do Trello pelo link ou pelo código do cartão e, se for imagem, pedir que a
          IA a descreva.
        </EmptyState>
      )
    ) : (
      <>
        {erro ? <LoadErrorBanner error={erro} onRetry={() => void carregarMais()} /> : null}
        <ul className={styles.anexos} aria-label="Anexos dos canais">
          {itens.map((a) => (
            <ItemDeAnexo key={a.id} a={a} agora={agora} aberto={aberto === a.id} anexando={anexando === a.id}
                         anexado={anexados.get(a.id)} obterArquivo={obterArquivo}
                         onAbrir={() => setAberto((atual) => (atual === a.id ? null : a.id))}
                         onAnexar={() => setAnexando(a.id)}
                         onCancelarAnexar={() => setAnexando(null)}
                         onAnexado={(jaEstava) => { setAnexando(null); setAnexados((m) => new Map(m).set(a.id, jaEstava)); }} />
          ))}
        </ul>
        <div className={styles.rodape}>
          <span className={styles.contagem}>Mostrando {itens.length} de {total}</span>
          {itens.length < total ? (
            <Button variant="outline" size="sm" loading={carregandoMais} onClick={() => void carregarMais()}>Carregar mais</Button>
          ) : null}
        </div>
      </>
    );
  } else if (erro) {
    corpo = <LoadErrorState what="os anexos" error={erro} onRetry={tentarDeNovo} />;
  } else {
    corpo = <LoadingRegion label="Carregando os anexos…"><Skeleton height={160} /></LoadingRegion>;
  }

  return (
    <div className={styles.abaAnexos}>
      <form className={styles.filtros} aria-label="Filtros dos anexos" onSubmit={(e) => e.preventDefault()}>
        <Field label="Canal" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} value={filtros.canal} onChange={(e) => mudar({ canal: e.target.value })}>
              <option value="">Todos</option>
              {Object.entries(CANAIS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Sentido" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} value={filtros.direcao} onChange={(e) => mudar({ direcao: e.target.value })}>
              <option value="">Recebidos e enviados</option>
              <option value="entrada">Recebidos</option>
              <option value="saida">Enviados pela Central</option>
            </Select>
          )}
        </Field>
        <Field label="Período" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} value={filtros.periodo} onChange={(e) => mudar({ periodo: e.target.value as PeriodoId })}>
              {PERIODOS.map((p) => <option key={p.id} value={p.id}>{p.rotulo}</option>)}
            </Select>
          )}
        </Field>
        <Checkbox label="Só o que eu mandei" checked={filtros.soDono} onChange={(e) => mudar({ soDono: e.target.checked })} />
      </form>
      {corpo}
    </div>
  );
}

function Miniatura({ tipo, motivo, url, falhou }: { tipo: Tipo; motivo: string | null; url: string | null; falhou: boolean }) {
  const [quebrou, setQuebrou] = useState(false);
  if (url && !quebrou) {
    return <img src={url} alt="" decoding="async" className={styles.imagem} onError={() => setQuebrou(true)} />;
  }
  // A imagem que ainda está chegando mostra o ícone de imagem; a que não veio, o de imagem quebrada.
  const Icone = quebrou || falhou || motivo ? ImageOff : tipo === 'imagem' ? ImageIcon : tipo === 'pdf' ? FileText : FileQuestion;
  return <span className={styles.icone}><Icone size={28} aria-hidden /></span>;
}

const custoLegivel = (usd: number) =>
  `US$ ${usd.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;

function ItemDeAnexo({ a, agora, aberto, anexando, anexado, obterArquivo, onAbrir, onAnexar, onCancelarAnexar, onAnexado }: {
  a: CanalAnexo;
  agora: number;
  aberto: boolean;
  anexando: boolean;
  /** `undefined`: não foi ao cartão nesta visita; `true`: já estava lá; `false`: anexado agora. */
  anexado: boolean | undefined;
  obterArquivo: ObterArquivo;
  onAbrir: () => void;
  onAnexar: () => void;
  onCancelarAnexar: () => void;
  onAnexado: (jaEstava: boolean) => void;
}) {
  const tipo = tipoDoAnexo(a);
  const motivo = semPrevia(a);
  const arquivo = useUrlDoArquivo(obterArquivo, a.id, a.tem_conteudo && tipo === 'imagem');
  const [imagemQuebrou, setImagemQuebrou] = useState(false);
  // "Ler pela IA" (F3/F5): a descrição gravada vem na lista; a nova só sai do botão de confirmar (chamada paga).
  const [descricao, setDescricao] = useState<string | null>(a.descricao);
  const [custo, setCusto] = useState<number | null>(null);
  const [ler, setLer] = useState<'fechado' | 'confirmando' | 'lendo'>('fechado');
  const [erroLer, setErroLer] = useState<string | null>(null);
  const nome = `${ROTULO_DO_TIPO[tipo]} ${a.id}`;

  const lerPelaIa = async () => {
    if (ler === 'lendo') return;
    setLer('lendo');
    setErroLer(null);
    try {
      const r = await api.canaisLerAnexo(a.id);
      setDescricao(r.descricao);
      setCusto(r.do_cache ? null : r.custo_usd);
      setLer('fechado');
    } catch (e) {
      // A mensagem da rota já vem em português (ex.: o teto do dia, ou a imagem que não é do dono).
      setErroLer(toApiError(e).message);
      setLer('confirmando');
    }
  };

  return (
    <li className={styles.anexo} data-expandido={aberto || undefined} aria-label={nome}>
      <div className={styles.anexoTopo}>
        {a.tem_conteudo ? (
          <button type="button" className={styles.miniatura} aria-expanded={aberto}
                  aria-label={`${aberto ? 'Fechar' : 'Ver'} a prévia: ${nome}`} onClick={onAbrir}>
            <Miniatura tipo={tipo} motivo={motivo} url={arquivo.url} falhou={arquivo.falhou} />
          </button>
        ) : (
          <span className={styles.miniatura} aria-hidden><Miniatura tipo={tipo} motivo={motivo} url={null} falhou={false} /></span>
        )}
        <div className={styles.anexoInfo}>
          <div className={styles.selos}>
            <Badge size="sm">{CANAIS[a.canal] ?? a.canal}</Badge>
            <Badge size="sm" tone={a.direcao === 'saida' ? 'info' : a.do_dono ? 'neutral' : 'warning'}>{origemDoAnexo(a)}</Badge>
            {a.estado === 'recusado' ? <Badge size="sm" tone="danger">Recusado</Badge> : null}
            {a.estado === 'apagado' ? <Badge size="sm" tone="muted">Apagado</Badge> : null}
          </div>
          <p className={styles.meta}>
            {ROTULO_DO_TIPO[tipo]} · {tamanhoLegivel(a.bytes)} · <span title={a.criado_em}>{tempoRelativo(a.criado_em, agora)}</span>
          </p>
          {motivo ? <p className={styles.motivo}>{motivo}</p> : null}
          <div className={styles.acoes}>
            {a.tem_conteudo ? (
              <Button size="sm" variant="outline" icon={aberto ? EyeOff : Eye} onClick={onAbrir}>{aberto ? 'Fechar prévia' : 'Ver prévia'}</Button>
            ) : null}
            {a.pode_ler && descricao == null && ler === 'fechado' ? (
              <Button size="sm" variant="outline" icon={ScanText} onClick={() => setLer('confirmando')}>Ler pela IA</Button>
            ) : null}
            {a.pode_ir_ao_cartao && !anexando && anexado === undefined ? (
              <Button size="sm" variant="outline" icon={Paperclip} onClick={onAnexar}>Anexar ao cartão</Button>
            ) : null}
          </div>
        </div>
      </div>
      {aberto && a.tem_conteudo ? (
        <div className={styles.previa}>
          {tipo === 'imagem' && arquivo.url && !imagemQuebrou ? (
            <img src={arquivo.url} alt={`Prévia: ${nome}`} className={styles.previaImagem} onError={() => setImagemQuebrou(true)} />
          ) : tipo === 'imagem' && (arquivo.falhou || imagemQuebrou) ? (
            <p className={styles.motivo}>Não consegui carregar a imagem: o arquivo pode ter saído da Central.</p>
          ) : tipo === 'imagem' ? (
            <p className={styles.motivo}>Carregando a imagem…</p>
          ) : (
            <div className={styles.previaPdf}>
              <FileText size={32} aria-hidden />
              <p>O navegador baixa o PDF em vez de abri-lo aqui, por segurança.</p>
              <a className={styles.baixar} href={canalAnexoConteudoUrl(a.id)} download={`anexo-${a.id}.pdf`}>
                <Download size={14} aria-hidden /> Baixar o PDF
              </a>
            </div>
          )}
        </div>
      ) : null}
      {ler !== 'fechado' && descricao == null ? (
        <div className={styles.anexar} role="group" aria-label={`Ler pela IA: ${nome}`}>
          <p className={styles.anexarResumo}>
            A IA descreve a imagem numa chamada paga, com teto por imagem. A descrição fica guardada: ler de novo não custa.
          </p>
          <div className={styles.anexarAcoes}>
            <Button size="sm" variant="primary" loading={ler === 'lendo'} onClick={() => void lerPelaIa()}>Confirmar e ler pela IA</Button>
            <Button size="sm" variant="ghost" disabled={ler === 'lendo'} onClick={() => { setLer('fechado'); setErroLer(null); }}>
              Cancelar
            </Button>
          </div>
          {erroLer ? <p className={styles.lerErro} role="alert">{erroLer}</p> : null}
        </div>
      ) : null}
      {descricao != null ? (
        <div className={styles.descricao}>
          <p className={styles.descricaoRotulo}>
            <ScanText size={14} aria-hidden /> Descrição pela IA{custo != null ? ` · custou ${custoLegivel(custo)}` : ''}
          </p>
          <p className={styles.descricaoTexto}>{descricao}</p>
        </div>
      ) : null}
      {anexando ? <AnexarAoCartao anexoId={a.id} onFeito={onAnexado} onCancelar={onCancelarAnexar} /> : null}
      {anexado !== undefined ? (
        <p className={styles.feito} role="status">
          <CircleCheck size={14} aria-hidden /> {anexado ? 'Já estava no cartão do Trello: nada foi anexado de novo.' : 'Anexado ao cartão do Trello.'}
        </p>
      ) : null}
    </li>
  );
}
