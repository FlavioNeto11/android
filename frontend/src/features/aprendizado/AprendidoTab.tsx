import { BookOpen, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { cx, formatInt } from '../../lib/format';
import { useUiStore } from '../../store/ui';
import { LoadErrorBanner, LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import { apiAprendizado, PROVAS_DO_LIVRO, PROVA_LABEL, type FiltroDoLivro } from './api';
import { lerFiltroDoEndereco, PARAMS_DO_FILTRO, queryDoFiltro } from './filtroNoEndereco';
import { hashDe } from '../../lib/rotas';
import { NOME_DO_APP_NAO_IDENTIFICADO, PACOTE_NAO_RESOLVIDO, type VisaoDeApps } from './apps';
import { AvisoDaHabilidade, ItemDoLivro, chaveDoItem } from './ItemDoLivro';
import {
  ESTADOS_DO_LIVRO, LIVRO_KINDS, ORIGENS, ORIGEM_LABEL, ROTULOS, ROTULO_DICA, ROTULO_LABEL, type EntradaDoLivro, type ListaDoLivro,
  type LivroKind, acoesDoItem, isEstadoDoLivro, isLivroKind, rotuloDoEstado, rotuloDoKind, textoDosOcultos,
  titulosDaLista,
} from './model';
import styles from './Aprendizado.module.css';

/** Quantos itens por tipo e estado (a memória conta lembranças, não linhas). */
function Contagem({ contagem }: { contagem: NonNullable<ListaDoLivro['contagem']> }) {
  const tipos = Object.entries(contagem).filter(([, porEstado]) => Object.keys(porEstado).length > 0);
  if (tipos.length === 0) return null;
  return (
    <div className={styles.resumo} role="group" aria-label="Contagem por tipo e estado">
      {tipos.map(([kind, porEstado]) => (
        <span key={kind} className={styles.resumoChip}>
          <strong>{rotuloDoKind(kind)}</strong>
          {Object.entries(porEstado).map(([estado, n]) => {
            // Os rótulos de estado fazem o plural com "s" (publicado → publicados, desligado → desligados).
            const rotulo = kind === 'memoria' ? 'lembranças' : estado === '-' ? 'sem estado'
              : `${rotuloDoEstado(isEstadoDoLivro(estado) ? estado : null).toLowerCase()}${n === 1 ? '' : 's'}`;
            return <span key={estado}>{formatInt(n)} {rotulo}</span>;
          })}
        </span>
      ))}
    </div>
  );
}

/** `?item=<kind>:<ref>`: o item que um link (uma relação, uma versão) quer mostrar; o `ref` pode ter `@` (habilidade). */
export function itemDoLink(valor: string | undefined): { kind: LivroKind; ref: string } | null {
  if (!valor) return null;
  const i = valor.indexOf(':');
  const kind = valor.slice(0, i);
  const ref = valor.slice(i + 1);
  return i > 0 && ref && isLivroKind(kind) ? { kind, ref } : null;
}

/**
 * O item que o link apontou, aberto no topo do catálogo (o Livro não tem rota por item: a lista é filtrada por
 * tipo, estado, app e origem, e o item pode nem estar nela). Lê o detalhe e mostra a mesma linha do catálogo.
 */
function ItemDoLink({ kind, refDoItem, onMudou }: { kind: LivroKind; refDoItem: string; onMudou: () => void }) {
  const [entrada, setEntrada] = useState<EntradaDoLivro | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [vez, setVez] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    setEntrada(null);
    setErro(null);
    apiAprendizado.detalhe(kind, refDoItem, ctl.signal)
      .then((d) => setEntrada(d.item))
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setErro(toLoadError(e).message);
      });
    return () => ctl.abort();
  }, [kind, refDoItem, vez]);
  return (
    <section className={styles.secao} aria-label="Item aberto pelo link">
      <p className={styles.secaoLead}>
        Item aberto pelo link: {rotuloDoKind(kind)} <span className={styles.mono}>{refDoItem}</span>.{' '}
        <a className={styles.linkAlvo} href={hashDe('aprendizado', { query: { aba: 'aprendido' } })}>Fechar</a>
      </p>
      {erro ? <p className={styles.erroInline}>{erro}</p> : null}
      {entrada ? (
        <ul className={styles.lista} aria-label="Item aberto">
          <ItemDoLivro entrada={entrada} acoes={acoesDoItem(entrada)} abrirDetalhe
                       onMudou={() => { setVez((v) => v + 1); onMudou(); }}
                       extra={entrada.kind === 'habilidade' ? <AvisoDaHabilidade naFila={false} /> : null} />
        </ul>
      ) : null}
    </section>
  );
}

/**
 * O catálogo unificado (ADR-054, decisão 3): receitas, fluxos, habilidades, memória (só a contagem) e os itens do
 * livro, com o estado, o efeito medido, o último uso e as decisões da pessoa — desligar, aposentar e reativar, sempre
 * com motivo. Habilidade tem ciclo próprio (publicar é sempre de uma pessoa): aqui só o caminho até ele, em
 * Configuração → Fluxos e receitas → Habilidades; a validada também aparece na fila Para aprovar.
 */
export function AprendidoTab() {
  // 31.144: os filtros vêm do endereço (e a escolha da pessoa o escreve), para recarregar ou mandar o link não os perder.
  const qTipo = useUiStore((s) => s.rota.query[PARAMS_DO_FILTRO.kind]);
  const qEstado = useUiStore((s) => s.rota.query[PARAMS_DO_FILTRO.state]);
  const qOrigem = useUiStore((s) => s.rota.query[PARAMS_DO_FILTRO.origem]);
  const qProva = useUiStore((s) => s.rota.query[PARAMS_DO_FILTRO.prova]);
  const qVisao = useUiStore((s) => s.rota.query[PARAMS_DO_FILTRO.rotulo]);
  const outros = useMemo(() => lerFiltroDoEndereco({ tipo: qTipo, estado: qEstado, origem: qOrigem, prova: qProva, visao: qVisao }),
    [qTipo, qEstado, qOrigem, qProva, qVisao]);
  // O app vem do link (`?aba=aprendido&app=<pacote>`), para a navegação do detalhe do app ao catálogo e de volta.
  const app = useUiStore((s) => s.rota.query.app) || undefined;
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const setFiltro = (muda: (f: FiltroDoLivro) => FiltroDoLivro) => trocarQuery(queryDoFiltro(muda(outros)));
  const doLink = itemDoLink(useUiStore((s) => s.rota.query.item));
  // RA-19: a escolha de "Apps" vale para o app em que foi feita. Trocar de app NO FILTRO volta ao padrão do servidor, em que o
  // app escolhido mostra o que tem: escolher o QA Messenger depois de "Produto" não fica vazio (o `visao` sai do endereço).
  // O `visao` do endereço vale para o app em que foi escolhido (`appDaVisao`); um link que troca de app o larga.
  const appDaVisao = useRef(app);
  const visaoVale = outros.rotulo === undefined || appDaVisao.current === app;
  useEffect(() => {
    if (outros.rotulo === undefined) appDaVisao.current = app;
    else if (!visaoVale) trocarQuery(queryDoFiltro({ rotulo: undefined }));
  }, [outros.rotulo, app, visaoVale, trocarQuery]);
  const rotuloVale = visaoVale ? outros.rotulo : undefined;
  // Os campos, não o objeto: soltar o `visao` que não vale não relê o livro à toa.
  const filtro = useMemo<FiltroDoLivro>(() => ({ kind: outros.kind, state: outros.state, origem: outros.origem, prova: outros.prova, app, rotulo: rotuloVale }),
    [outros.kind, outros.state, outros.origem, outros.prova, app, rotuloVale]);
  const [visao, setVisao] = useState<VisaoDeApps | null>(null);
  const [lista, setLista] = useState<ListaDoLivro | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const vez = useRef(0);

  const carregar = useCallback(async (f: FiltroDoLivro) => {
    const minha = ++vez.current;
    setCarregando(true);
    try {
      const res = await apiAprendizado.livro(f);
      if (minha !== vez.current) return;
      // 31.131: o servidor filtra pela marca; a guarda aqui cobre o backend que ainda ignora o parâmetro (a marca vem em cada linha).
      const todos = Array.isArray(res?.itens) ? res.itens : [];
      const itens = f.prova === 'so_prova' ? todos.filter((i) => i.nascido_de_prova === true)
        : f.prova === 'sem_prova' ? todos.filter((i) => i.nascido_de_prova !== true) : todos;
      setLista({ itens, total: res?.total ?? 0, contagem: res?.contagem,
                 rotulo: res?.rotulo, ocultos: res?.ocultos });
      setErro(null);
    } catch (e) {
      if (minha === vez.current) setErro(toLoadError(e));
    } finally {
      if (minha === vez.current) setCarregando(false);
    }
  }, []);

  useEffect(() => {
    void carregar(filtro);
  }, [carregar, filtro]);

  // As opções do filtro de app vêm da lista de /apps (nenhum pacote fica escrito aqui); se ela falhar, o filtro só
  // mostra o app do link e o resto do catálogo continua funcionando.
  useEffect(() => {
    const ctl = new AbortController();
    apiAprendizado.apps(ctl.signal).then(setVisao).catch(() => undefined);
    return () => ctl.abort();
  }, []);
  const opcoesDeApp = useMemo(() => {
    const o = (visao?.apps ?? []).map((a) => ({ pacote: a.pacote, nome: a.nome }));
    if (visao?.nao_resolvido && visao.nao_resolvido.aprendido.total > 0) o.push({ pacote: visao.nao_resolvido.pacote || PACOTE_NAO_RESOLVIDO, nome: NOME_DO_APP_NAO_IDENTIFICADO });
    if (app && !o.some((x) => x.pacote === app)) o.push({ pacote: app, nome: app });
    return o;
  }, [visao, app]);
  const titulos = useMemo(() => titulosDaLista(lista?.itens ?? []), [lista]);
  // RA-19: o conjunto que valeu é o escolhido, senão o que o servidor aplicou (sem app, o padrão esconde o de teste).
  const rotulo = filtro.rotulo ?? lista?.rotulo ?? (app ? 'todos' : 'produto');
  const ocultos = textoDosOcultos(rotulo, lista?.ocultos);
  const idDosApps = useId();

  return (
    <section className={styles.secao} aria-label="Aprendido">
      <div className={styles.toolbar}>
        <Field label="Tipo" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={filtro.kind ?? ''}
                    onChange={(e) => setFiltro((f) => ({ ...f, kind: isLivroKind(e.target.value) ? e.target.value : undefined }))}>
              <option value="">Todos</option>
              {LIVRO_KINDS.map((k) => <option key={k} value={k}>{rotuloDoKind(k)}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Estado" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={filtro.state ?? ''}
                    onChange={(e) => setFiltro((f) => ({ ...f, state: isEstadoDoLivro(e.target.value) ? e.target.value : undefined }))}>
              <option value="">Todos</option>
              {ESTADOS_DO_LIVRO.map((s) => <option key={s} value={s}>{rotuloDoEstado(s)}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Aplicativo" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={app ?? ''} onChange={(e) => trocarQuery({ app: e.target.value || undefined, ...queryDoFiltro({ rotulo: undefined }) })}>
              <option value="">Todos</option>
              {opcoesDeApp.map((a) => (
                <option key={a.pacote} value={a.pacote}>
                  {a.pacote === PACOTE_NAO_RESOLVIDO ? NOME_DO_APP_NAO_IDENTIFICADO : a.nome === a.pacote ? a.pacote : `${a.nome} (${a.pacote})`}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Origem" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={filtro.origem ?? ''}
                    onChange={(e) => setFiltro((f) => ({ ...f, origem: ORIGENS.find((o) => o === e.target.value) }))}>
              <option value="">Todas</option>
              {ORIGENS.map((o) => <option key={o} value={o}>{ORIGEM_LABEL[o]}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Prova" className={styles.filtro}>
          {({ id }) => (
            <Select id={id} small value={filtro.prova ?? ''}
                    onChange={(e) => setFiltro((f) => ({ ...f, prova: PROVAS_DO_LIVRO.find((p) => p === e.target.value) }))}>
              <option value="">Todos</option>
              {PROVAS_DO_LIVRO.map((p) => <option key={p} value={p}>{PROVA_LABEL[p]}</option>)}
            </Select>
          )}
        </Field>
        <div className={styles.filtroDeApps}>
          <span id={idDosApps} className={styles.filtroDeAppsNome}>Apps</span>
          <div className={styles.segmentado}>
            <div className={styles.segmentos} role="radiogroup" aria-labelledby={idDosApps}>
              {ROTULOS.map((r) => (
                <button key={r} type="button" role="radio" aria-checked={rotulo === r} title={ROTULO_DICA[r]}
                        className={cx(styles.segmento, rotulo === r && styles.segmentoOn)}
                        onClick={() => { appDaVisao.current = app; trocarQuery(queryDoFiltro({ rotulo: r })); }}>
                  {ROTULO_LABEL[r]}
                </button>
              ))}
            </div>
            {ocultos ? <span className={styles.ocultos}>{ocultos}</span> : null}
          </div>
        </div>
        <div className={styles.toolbarFim}>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar(filtro)}>Atualizar</Button>
        </div>
      </div>

      {doLink ? <ItemDoLink kind={doLink.kind} refDoItem={doLink.ref} onMudou={() => void carregar(filtro)} /> : null}
      {erro && lista ? <LoadErrorBanner error={erro} onRetry={() => void carregar(filtro)} /> : null}
      {!lista ? (
        erro ? <LoadErrorState what="o livro de aprendizado" error={erro} onRetry={() => void carregar(filtro)} /> : (
          <LoadingRegion label="Carregando o livro de aprendizado…" className={styles.secao}>
            <Skeleton height={32} radius={8} />
            <Skeleton height={72} radius={8} />
          </LoadingRegion>
        )
      ) : (
        <>
          {lista.contagem ? <Contagem contagem={lista.contagem} /> : null}
          {lista.itens.length === 0 ? (
            <EmptyState icon={BookOpen} compact title="Nada aprendido com este filtro"
                        hint={ocultos ? `Há ${ocultos} pelo filtro de apps: escolha "Todos" para vê-los.` : undefined} />
          ) : (
            <ul className={styles.lista} aria-label="Catálogo do aprendizado">
              {lista.itens.map((e) => (
                <ItemDoLivro
                  key={chaveDoItem(e)}
                  entrada={e}
                  titulo={titulos.get(e)}
                  acoes={acoesDoItem(e)}
                  onMudou={() => void carregar(filtro)}
                  extra={e.kind === 'habilidade' ? <AvisoDaHabilidade naFila={false} /> : null}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
