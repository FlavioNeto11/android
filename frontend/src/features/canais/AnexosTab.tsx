import { CircleCheck, Download, Eye, EyeOff, FileQuestion, FileText, ImageOff, Paperclip } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, canalAnexoConteudoUrl } from '../../api/client';
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

/**
 * A aba Anexos da tela Canais (item 28.24, fatia 4): o que passou pelos canais como arquivo, do mais novo ao mais velho.
 * Imagem e PDF têm prévia ao clicar; o arquivo que o dono mandou pode ir a um cartão do Trello, com confirmação na linha.
 *
 * FALTA (fatia 3, entra num ajuste depois que as duas estiverem na main): o botão "Ler" e a descrição do anexo pela IA.
 * Esta tela não chama IA nenhuma e não mostra nome de remetente: o produto não guarda esse dado.
 */
export function AnexosTab() {
  const [filtros, setFiltros] = useState<Filtros>(SEM_FILTROS);
  const [pagina, setPagina] = useState<CanalAnexosPagina | null>(null);
  const [itens, setItens] = useState<CanalAnexo[]>([]);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregandoMais, setCarregandoMais] = useState(false);
  const [aberto, setAberto] = useState<number | null>(null);
  const [anexando, setAnexando] = useState<number | null>(null);
  const [anexados, setAnexados] = useState<ReadonlySet<number>>(new Set());
  const token = useRef(0);
  const agora = useNow();

  const consulta = useCallback((f: Filtros, offset: number) => api.canaisAnexos({
    canal: f.canal || undefined,
    direcao: f.direcao || undefined,
    do_dono: f.soDono ? 'true' : undefined,
    desde: desdeDoPeriodo(f.periodo, Date.now()),
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
          Os arquivos que você manda aos canais, e os que a Central envia, ficam listados nesta aba.
        </EmptyState>
      )
    ) : (
      <>
        {erro ? <LoadErrorBanner error={erro} onRetry={() => void carregarMais()} /> : null}
        <ul className={styles.anexos} aria-label="Anexos dos canais">
          {itens.map((a) => (
            <ItemDeAnexo key={a.id} a={a} agora={agora} aberto={aberto === a.id} anexando={anexando === a.id}
                         anexado={anexados.has(a.id)}
                         onAbrir={() => setAberto((atual) => (atual === a.id ? null : a.id))}
                         onAnexar={() => setAnexando(a.id)}
                         onCancelarAnexar={() => setAnexando(null)}
                         onAnexado={() => { setAnexando(null); setAnexados((s) => new Set(s).add(a.id)); }} />
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

function Miniatura({ a, motivo }: { a: CanalAnexo; motivo: string | null }) {
  const [quebrou, setQuebrou] = useState(false);
  const tipo = tipoDoAnexo(a);
  if (a.tem_conteudo && tipo === 'imagem' && !quebrou) {
    return <img src={canalAnexoConteudoUrl(a.id)} alt="" loading="lazy" decoding="async" className={styles.imagem} onError={() => setQuebrou(true)} />;
  }
  const Icone = quebrou || motivo ? ImageOff : tipo === 'pdf' ? FileText : FileQuestion;
  return <span className={styles.icone}><Icone size={28} aria-hidden /></span>;
}

function ItemDeAnexo({ a, agora, aberto, anexando, anexado, onAbrir, onAnexar, onCancelarAnexar, onAnexado }: {
  a: CanalAnexo;
  agora: number;
  aberto: boolean;
  anexando: boolean;
  anexado: boolean;
  onAbrir: () => void;
  onAnexar: () => void;
  onCancelarAnexar: () => void;
  onAnexado: () => void;
}) {
  const tipo = tipoDoAnexo(a);
  const motivo = semPrevia(a);
  const [imagemQuebrou, setImagemQuebrou] = useState(false);
  const nome = `${ROTULO_DO_TIPO[tipo]} ${a.id}`;
  return (
    <li className={styles.anexo} data-expandido={aberto || undefined} aria-label={nome}>
      <div className={styles.anexoTopo}>
        {a.tem_conteudo ? (
          <button type="button" className={styles.miniatura} aria-expanded={aberto}
                  aria-label={`${aberto ? 'Fechar' : 'Ver'} a prévia: ${nome}`} onClick={onAbrir}>
            <Miniatura a={a} motivo={motivo} />
          </button>
        ) : (
          <span className={styles.miniatura} aria-hidden><Miniatura a={a} motivo={motivo} /></span>
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
            {a.pode_ir_ao_cartao && !anexando && !anexado ? (
              <Button size="sm" variant="outline" icon={Paperclip} onClick={onAnexar}>Anexar ao cartão</Button>
            ) : null}
          </div>
        </div>
      </div>
      {aberto && a.tem_conteudo ? (
        <div className={styles.previa}>
          {tipo === 'imagem' && !imagemQuebrou ? (
            <img src={canalAnexoConteudoUrl(a.id)} alt={`Prévia: ${nome}`} className={styles.previaImagem} onError={() => setImagemQuebrou(true)} />
          ) : tipo === 'imagem' ? (
            <p className={styles.motivo}>Não consegui carregar a imagem: o arquivo pode ter saído da Central.</p>
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
      {anexando ? <AnexarAoCartao anexoId={a.id} onFeito={onAnexado} onCancelar={onCancelarAnexar} /> : null}
      {anexado ? <p className={styles.feito} role="status"><CircleCheck size={14} aria-hidden /> Anexado ao cartão do Trello.</p> : null}
    </li>
  );
}
