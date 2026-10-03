import { BookOpen, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatInt } from '../../lib/format';
import { useUiStore } from '../../store/ui';
import { LoadErrorBanner, LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import { apiAprendizado, type FiltroDoLivro } from './api';
import { hashDe } from '../../lib/rotas';
import type { VisaoDeApps } from './apps';
import { AvisoDaHabilidade, ItemDoLivro, chaveDoItem } from './ItemDoLivro';
import {
  ESTADOS_DO_LIVRO, LIVRO_KINDS, ORIGENS, ORIGEM_LABEL, type EntradaDoLivro, type ListaDoLivro, type LivroKind, acoesDoItem, isEstadoDoLivro, isLivroKind,
  rotuloDoEstado, rotuloDoKind, titulosDaLista,
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
  const [outros, setFiltro] = useState<FiltroDoLivro>({});
  // O app vem do link (`?aba=aprendido&app=<pacote>`), para a navegação do detalhe do app ao catálogo e de volta.
  const app = useUiStore((s) => s.rota.query.app) || undefined;
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const doLink = itemDoLink(useUiStore((s) => s.rota.query.item));
  const filtro = useMemo<FiltroDoLivro>(() => ({ ...outros, app }), [outros, app]);
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
      setLista({ itens: Array.isArray(res?.itens) ? res.itens : [], total: res?.total ?? 0, contagem: res?.contagem });
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
    if (visao?.nao_resolvido && visao.nao_resolvido.aprendido.total > 0) o.push({ pacote: visao.nao_resolvido.pacote || 'nao_resolvido', nome: 'App não resolvido' });
    if (app && !o.some((x) => x.pacote === app)) o.push({ pacote: app, nome: app });
    return o;
  }, [visao, app]);
  const titulos = useMemo(() => titulosDaLista(lista?.itens ?? []), [lista]);

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
            <Select id={id} small value={app ?? ''} onChange={(e) => trocarQuery({ app: e.target.value || undefined })}>
              <option value="">Todos</option>
              {opcoesDeApp.map((a) => <option key={a.pacote} value={a.pacote}>{a.nome === a.pacote ? a.pacote : `${a.nome} (${a.pacote})`}</option>)}
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
            <EmptyState icon={BookOpen} compact title="Nada aprendido com este filtro" />
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
