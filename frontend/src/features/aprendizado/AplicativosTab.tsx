import { AppWindow, ArrowLeft, RefreshCw } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatInt } from '../../lib/format';
import { LoadErrorBanner, LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import { useUiStore } from '../../store/ui';
import { apiAprendizado } from './api';
import {
  EXISTENCIA_META, abrirApp, abrirAprendidoDoApp, linhaDeUso, modosEmTexto, resumoDoAprendido, resumoDoDeclarado, rotuloDoArquivo, rotuloDoUso,
  temMedidoNaoUsado, type Contagem, type DetalheDoApp, type ResumoDoApp, type VisaoDeApps,
} from './apps';
import { ItemDoLivro, chaveDoItem } from './ItemDoLivro';
import { acoesDoItem, rotuloDoKind } from './model';
import styles from './Aprendizado.module.css';

/** O nome do balde `nao_resolvido` (o que o backend não conseguiu ligar a um pacote). */
const NAO_RESOLVIDO = 'nao_resolvido';

function LinhaDeContagem({ rotulo, c }: { rotulo: string; c: Contagem }) {
  const linhas = resumoDoAprendido(c);
  return (
    <div className={styles.cartaoLinha}>
      <span className={styles.cartaoRotulo}>{rotulo}</span>
      {linhas.length === 0 ? <span>Nada ainda</span> : linhas.map((l) => <span key={l.tipo}><strong>{l.tipo}</strong>: {l.texto}</span>)}
    </div>
  );
}

/** Um app no Global: o que se declarou, o que se aprendeu, o que foi absorvido e como o aprendido é usado. */
function CartaoDoApp({ app, balde }: { app: ResumoDoApp; balde?: boolean }) {
  const meta = app.existencia ? EXISTENCIA_META[app.existencia] : null;
  const declarado = resumoDoDeclarado(app.declarado);
  const vazio = app.aprendido.total === 0 && app.absorvido === 0 && !app.declarado;
  return (
    <li className={vazio ? `${styles.cartao} ${styles.cartaoVazio}` : styles.cartao} data-app={app.pacote}>
      <div className={styles.cartaoHead}>
        <span className={styles.cartaoNome}>{balde ? 'App não resolvido' : app.nome}</span>
        {meta ? <Badge tone={meta.tone} size="sm" title={meta.dica}>{meta.label}</Badge>
          : balde ? <Badge tone="warning" size="sm" title="Itens do Livro que o sistema não conseguiu ligar a um pacote.">sem pacote</Badge> : null}
        {app.declarado?.login_gerenciado ? <Badge tone="info" size="sm" title="O login deste app é feito fora da IA.">login gerenciado</Badge> : null}
      </div>
      {!balde ? <span className={styles.mono}>{app.pacote}</span> : null}
      <div className={styles.cartaoLinha}>
        <span className={styles.cartaoRotulo}>Declarado</span>
        <span>{declarado ?? (balde ? 'Não se aplica' : 'Nada declarado')}</span>
      </div>
      <LinhaDeContagem rotulo={`Aprendido (${formatInt(app.aprendido.total)})`} c={app.aprendido.contagem} />
      <div className={styles.cartaoLinha}>
        <span className={styles.cartaoRotulo}>Absorvido pelo repositório</span>
        <span>{formatInt(app.absorvido)}</span>
      </div>
      <div className={styles.cartaoLinha}>
        <span className={styles.cartaoRotulo}>Como é usado</span>
        <span className={styles.cartaoUso}>{linhaDeUso(app.uso)}</span>
      </div>
      {temMedidoNaoUsado(app.uso) ? (
        <div><Badge tone="warning" size="sm" title="Em modo sombra ou observação o sistema mede, mas não usa.">medido, não usado</Badge></div>
      ) : null}
      <div>
        <Button size="sm" variant="secondary" onClick={() => abrirApp(app.pacote)}>Ver {balde ? 'o balde' : 'o app'}</Button>
      </div>
    </li>
  );
}

function ForaDoEixo({ c }: { c: Contagem }) {
  const linhas = resumoDoAprendido(c);
  if (linhas.length === 0) return null;
  return (
    <li className={styles.cartao} data-app="fora_do_eixo">
      <div className={styles.cartaoHead}>
        <span className={styles.cartaoNome}>Fora do eixo de app</span>
        <Badge tone="neutral" size="sm">memória e lições gerais</Badge>
      </div>
      <p className={styles.secaoLead}>Memória, voz, preferências e lições sem app não pertencem a nenhum aplicativo; a memória fica com a persona.</p>
      <div className={styles.cartaoLinha}>
        {linhas.map((l) => <span key={l.tipo}><strong>{l.tipo}</strong>: {l.texto}</span>)}
      </div>
    </li>
  );
}

/** Carrega com cancelamento por geração: a resposta velha nunca sobrescreve a nova. */
function useCarga<T>(buscar: (signal: AbortSignal) => Promise<T>, chave: string) {
  const [dado, setDado] = useState<T | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const [carregando, setCarregando] = useState(true);
  const vez = useRef(0);
  const buscarRef = useRef(buscar);
  buscarRef.current = buscar;
  const carregar = useCallback(async () => {
    const minha = ++vez.current;
    setCarregando(true);
    try {
      const res = await buscarRef.current(new AbortController().signal);
      if (minha !== vez.current) return;
      setDado(res);
      setErro(null);
    } catch (e) {
      if (minha === vez.current) setErro(toLoadError(e));
    } finally {
      if (minha === vez.current) setCarregando(false);
    }
  }, []);
  useEffect(() => {
    setDado(null);
    void carregar();
  }, [carregar, chave]);
  return { dado, erro, carregando, carregar };
}

function Global() {
  const { dado: visao, erro, carregando, carregar } = useCarga<VisaoDeApps>((s) => apiAprendizado.apps(s), 'global');
  return (
    <section className={styles.secao} aria-label="Aplicativos">
      <div className={styles.toolbar}>
        <p className={styles.secaoLead}>
          O aprendizado de cada aplicativo: o que foi declarado, o que o sistema aprendeu, o que o repositório absorveu e como
          cada coisa é usada. Um app sem nada aparece com zeros.
        </p>
        <div className={styles.toolbarFim}>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>Atualizar</Button>
        </div>
      </div>
      {erro && visao ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}
      {!visao ? (
        erro ? <LoadErrorState what="a visão por aplicativo" error={erro} onRetry={() => void carregar()} /> : (
          <LoadingRegion label="Carregando os aplicativos…" className={styles.secao}>
            <Skeleton height={32} radius={8} />
            <Skeleton height={120} radius={8} />
          </LoadingRegion>
        )
      ) : (
        <>
          <Disclosure summary="Como o aprendizado é usado hoje (modos globais)" bare>
            {() => (
              <div className={styles.resumo} role="group" aria-label="Modos globais">
                {modosEmTexto(visao.modos).map((m) => (
                  <span key={m.tipo} className={styles.resumoChip}><strong>{m.tipo}</strong><span>{m.modo}</span></span>
                ))}
              </div>
            )}
          </Disclosure>
          {visao.apps.length === 0 && !visao.nao_resolvido ? (
            <EmptyState icon={AppWindow} compact title="Nenhum aplicativo conhecido" />
          ) : null}
          <ul className={styles.cartoes} aria-label="Aplicativos">
            {visao.apps.map((a) => <CartaoDoApp key={a.pacote} app={a} />)}
            {visao.nao_resolvido && visao.nao_resolvido.aprendido.total + visao.nao_resolvido.absorvido > 0
              ? <CartaoDoApp app={{ ...visao.nao_resolvido, pacote: visao.nao_resolvido.pacote || NAO_RESOLVIDO }} balde /> : null}
            <ForaDoEixo c={visao.fora_do_eixo} />
          </ul>
        </>
      )}
    </section>
  );
}

function Declarado({ d }: { d: DetalheDoApp }) {
  return (
    <section className={styles.secao} aria-label="O que o sistema sabe por declaração">
      <h3 className={styles.secaoTitulo}>Declarado</h3>
      <p className={styles.secaoLead}>Os arquivos que descrevem o app, quanto cada um traz e como é usado.</p>
      {d.declarado.length === 0 ? <p className={styles.secaoLead}>Nada foi declarado para este app.</p> : (
        <ul className={styles.tabelaDeclarado}>
          {d.declarado.map((i) => (
            <li key={i.tipo} className={styles.item}>
              <div className={styles.itemHead}>
                <span className={styles.itemTitulo}>{rotuloDoArquivo(i.tipo)}</span>
                <Badge tone={i.presente ? 'success' : 'neutral'} size="sm">{i.presente ? 'presente' : 'ausente'}</Badge>
              </div>
              <div className={styles.itemMeta}>
                {i.arquivo ? <span className={styles.mono}>{i.arquivo}</span> : null}
                {i.quantidade !== null ? <span>Quantidade: {formatInt(i.quantidade)}</span> : null}
                <span title={i.uso?.porque ?? undefined}>Uso: {i.uso ? rotuloDoUso(i.uso.camada) : '—'}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ListaDoApp({ titulo, lead, itens, vazio, onMudou }: {
  titulo: string; lead: string; itens: DetalheDoApp['aprendido']; vazio: string; onMudou?: () => void;
}) {
  return (
    <section className={styles.secao} aria-label={titulo}>
      <h3 className={styles.secaoTitulo}>{titulo} ({formatInt(itens.length)})</h3>
      <p className={styles.secaoLead}>{lead}</p>
      {itens.length === 0 ? <p className={styles.secaoLead}>{vazio}</p> : (
        <ul className={styles.lista} aria-label={titulo}>
          {itens.map((e) => (
            <ItemDoLivro
              key={chaveDoItem(e)}
              entrada={e}
              acoes={onMudou ? acoesDoItem(e) : []}
              onMudou={onMudou ?? (() => undefined)}
              extra={e.uso ? (
                <p className={styles.secaoLead} title={e.uso.porque ?? undefined}>
                  {rotuloDoKind(e.kind)} · uso: {rotuloDoUso(e.uso.camada)}{e.absorvida_em ? ` · absorvido em ${e.absorvida_em}` : ''}
                </p>
              ) : null}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function DetalheDeUmApp({ pacote }: { pacote: string }) {
  const { dado: d, erro, carregando, carregar } = useCarga<DetalheDoApp>((s) => apiAprendizado.app(pacote, s), pacote);
  const voltar = () => useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'apps' } });
  const balde = pacote === NAO_RESOLVIDO;
  const nome = d ? (balde ? 'App não resolvido' : d.app.nome) : balde ? 'App não resolvido' : pacote;
  const meta = d?.app.existencia ? EXISTENCIA_META[d.app.existencia] : null;
  return (
    <section className={styles.secao} aria-label={`Aplicativo ${nome}`}>
      <nav className={styles.migalhas} aria-label="Onde você está">
        <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={voltar}>Todos os aplicativos</Button>
        <span aria-hidden>›</span>
        <strong>{nome}</strong>
        {!balde ? <span className={styles.mono}>{pacote}</span> : null}
        {meta ? <Badge tone={meta.tone} size="sm" title={meta.dica}>{meta.label}</Badge> : null}
        <div className={styles.toolbarFim}>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>Atualizar</Button>
        </div>
      </nav>
      {erro && d ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}
      {!d ? (
        erro ? <LoadErrorState what="este aplicativo" error={erro} onRetry={() => void carregar()} /> : (
          <LoadingRegion label="Carregando o aplicativo…" className={styles.secao}>
            <Skeleton height={32} radius={8} />
            <Skeleton height={120} radius={8} />
          </LoadingRegion>
        )
      ) : (
        <>
          <p className={styles.secaoLead}>Como é usado: {linhaDeUso(d.app.uso)}</p>
          {!balde ? <Declarado d={d} /> : null}
          <ListaDoApp
            titulo="Aprendido"
            lead="O que o sistema aprendeu deste app, com o estado e a decisão da pessoa."
            itens={d.aprendido}
            vazio="Nada aprendido para este app."
            onMudou={() => void carregar()}
          />
          <ListaDoApp
            titulo="Absorvido"
            lead="O que já virou conhecimento declarado do repositório: não conta mais como aprendido."
            itens={d.absorvido}
            vazio="Nada absorvido para este app."
          />
          <div>
            <Button size="sm" variant="secondary" onClick={() => abrirAprendidoDoApp(pacote)}>Ver no catálogo Aprendido</Button>
          </div>
        </>
      )}
    </section>
  );
}

/** A aba Aplicativos: o Global (`?aba=apps`) e o detalhe de um app (`?aba=apps&app=<pacote>`). */
export function AplicativosTab() {
  const pacote = useUiStore((s) => s.rota.query.app);
  return pacote ? <DetalheDeUmApp pacote={pacote} /> : <Global />;
}
