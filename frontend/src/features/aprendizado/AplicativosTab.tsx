import { AppWindow, ArrowLeft, RefreshCw } from 'lucide-react';
import { useMemo } from 'react';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { formatInt } from '../../lib/format';
import { LoadErrorBanner, LoadErrorState } from '../../lib/loadError';
import { useUiStore } from '../../store/ui';
import { apiAprendizado } from './api';
import {
  EXISTENCIA_META, TIPOS_COM_MODO_POR_APP, abrirApp, abrirAprendidoDoApp, excecoesPorApp, linhaDeUso, modosDoAppEmTexto,
  modosEmTexto, modosProprios, trechoDeConfig, usoPorTipo, resumoDoAprendido, resumoDoDeclarado, rotuloDoArquivo, rotuloDoUso,
  temMedidoNaoUsado, valoresDoModo, type Contagem,
  type DetalheDoApp, type ModosDoApp, type ResumoDoApp, type VisaoDeApps,
} from './apps';
import { contarPorRotulo, doApp, gruposDoAprendido } from './atencao';
import { ItemDoLivro, chaveDoItem } from './ItemDoLivro';
import { acoesDoItem, rotuloDoKind, titulosDaLista, type EntradaDoLivro } from './model';
import { ChipsDeSaude, FalhasDoApp, FilaDeAtencao, quantosPedemAtencao } from './SaudeDoApp';
import { useCarga } from './useCarga';
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

const textoDeAtencao = (n: number) => `${formatInt(n)} ${n === 1 ? 'pede' : 'pedem'} atenção`;

/** Um app no Global: o que se declarou, o que se aprendeu, o que foi absorvido e como o aprendido é usado. */
/**
 * `itens` são os itens do Livro deste app (a lista traz a saúde de cada um); `undefined` = a lista não carregou, e o
 * cartão não mostra a linha de saúde em vez de inventar um zero.
 */
function CartaoDoApp({ app, balde, itens }: { app: ResumoDoApp; balde?: boolean; itens?: EntradaDoLivro[] }) {
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
        {usoPorTipo(app.uso).length === 0 ? <span className={styles.cartaoUso}>{linhaDeUso(app.uso)}</span>
          : usoPorTipo(app.uso).map((t) => <span key={t.tipo} className={styles.cartaoUso}><strong>{t.tipo}</strong>: {t.texto}</span>)}
      </div>
      {modosProprios(app.modos_do_app).length > 0 ? (
        <div className={styles.cartaoLinha} data-modo-proprio>
          <span className={styles.cartaoRotulo}>Modo próprio deste app</span>
          <span className={styles.chipsDeModo}>
            {modosProprios(app.modos_do_app).map((m) => (
              <Badge key={m.chave} tone="info" size="sm" title={m.efeito ?? undefined}>{m.tipo}: {m.modo}</Badge>
            ))}
          </span>
        </div>
      ) : null}
      {itens ? (
        <div className={styles.cartaoLinha}>
          <span className={styles.cartaoRotulo}>Saúde do aprendido</span>
          <ChipsDeSaude itens={itens} />
          {quantosPedemAtencao(itens) > 0 ? (
            <span><Badge tone="warning" size="sm" title="Degradando, provavelmente obsoleto ou sem evidência: veja a fila Atenção.">
              {textoDeAtencao(quantosPedemAtencao(itens))}
            </Badge></span>
          ) : null}
        </div>
      ) : null}
      {temMedidoNaoUsado(app.uso) ? (
        <div><Badge tone="warning" size="sm" title="Em modo sombra ou observação o sistema mede, mas não usa.">medido, não usado</Badge></div>
      ) : null}
      <div>
        <Button size="sm" variant="secondary" onClick={() => abrirApp(app.pacote)}>Ver {balde ? 'o balde' : 'o app'}</Button>
      </div>
    </li>
  );
}

/** Como o aprendido do app é usado, um chip por tipo (a frase longa de `linhaDeUso` não se lê de relance). */
function UsoEmChips({ uso }: { uso: Contagem }) {
  const tipos = usoPorTipo(uso);
  if (tipos.length === 0) return <span>Nada aprendido para usar ainda.</span>;
  return (
    <span className={styles.resumo} role="group" aria-label="Como é usado">
      {tipos.map((t) => (
        <span key={t.tipo} className={styles.resumoChip}><strong>{t.tipo}</strong><span>{t.texto}</span></span>
      ))}
    </span>
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

function Global() {
  const { dado: visao, erro, carregando, carregar } = useCarga<VisaoDeApps>((s) => apiAprendizado.apps(s), 'global');
  // A saúde vem da lista do Livro (a visão por app não a traz). Se ela falhar, os cartões seguem sem a linha de saúde.
  const livro = useCarga((s) => apiAprendizado.livro({}, s), 'livro-global');
  const todos = livro.dado ? (Array.isArray(livro.dado.itens) ? livro.dado.itens : []) : undefined;
  const nomes = useMemo(() => new Map((visao?.apps ?? []).map((a) => [a.pacote, a.nome] as const)), [visao]);
  const excecoes = useMemo(() => (visao ? excecoesPorApp(visao.modos, nomes) : []), [visao, nomes]);
  const recarregar = () => { void carregar(); void livro.carregar(); };
  return (
    <section className={styles.secao} aria-label="Aplicativos">
      <div className={styles.toolbar}>
        <p className={styles.secaoLead}>
          O aprendizado de cada aplicativo: o que foi declarado, o que o sistema aprendeu, o que o repositório absorveu e como
          cada coisa é usada. Um app sem nada aparece com zeros.
        </p>
        <div className={styles.toolbarFim}>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={carregando || livro.carregando} onClick={recarregar}>Atualizar</Button>
        </div>
      </div>
      {erro && visao ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}
      {livro.erro && visao ? <LoadErrorBanner error={livro.erro} onRetry={() => void livro.carregar()} /> : null}
      {!visao ? (
        erro ? <LoadErrorState what="a visão por aplicativo" error={erro} onRetry={() => void carregar()} /> : (
          <LoadingRegion label="Carregando os aplicativos…" className={styles.secao}>
            <Skeleton height={32} radius={8} />
            <Skeleton height={120} radius={8} />
          </LoadingRegion>
        )
      ) : (
        <>
          <Disclosure
            summary={excecoes.length > 0
              ? `Como o aprendizado é usado hoje (modos globais e ${excecoes.length} ${excecoes.length === 1 ? 'exceção' : 'exceções'} por app)`
              : 'Como o aprendizado é usado hoje (modos globais)'}
            bare
          >
            {() => (
              <>
                <div className={styles.resumo} role="group" aria-label="Modos globais">
                  {modosEmTexto(visao.modos).map((m) => (
                    <span key={m.tipo} className={styles.resumoChip}><strong>{m.tipo}</strong><span>{m.modo}</span></span>
                  ))}
                </div>
                {excecoes.length > 0 ? (
                  <div className={styles.excecoes} data-excecoes-por-app>
                    <span className={styles.cartaoRotulo}>Exceções por app (lições e telas)</span>
                    <ul aria-label="Exceções por app">
                      {excecoes.map((x) => (
                        <li key={`${x.pacote}:${x.tipo}`}>
                          <a className={styles.linkAlvo} href={`#/aprendizado?aba=apps&app=${encodeURIComponent(x.pacote)}`}>{x.app}</a>
                          {' — '}<strong>{x.tipo}</strong>: {x.modo}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </>
            )}
          </Disclosure>
          {visao.apps.length === 0 && !visao.nao_resolvido ? (
            <EmptyState icon={AppWindow} compact title="Nenhum aplicativo conhecido" />
          ) : null}
          <ul className={styles.cartoes} aria-label="Aplicativos">
            {visao.apps.map((a) => <CartaoDoApp key={a.pacote} app={a} itens={todos ? doApp(todos, a.pacote) : undefined} />)}
            {visao.nao_resolvido && visao.nao_resolvido.aprendido.total + visao.nao_resolvido.absorvido > 0
              ? <CartaoDoApp app={{ ...visao.nao_resolvido, pacote: visao.nao_resolvido.pacote || NAO_RESOLVIDO }} balde
                             itens={todos ? doApp(todos, visao.nao_resolvido.pacote || NAO_RESOLVIDO) : undefined} /> : null}
            <ForaDoEixo c={visao.fora_do_eixo} />
          </ul>
          {todos ? <FilaDeAtencao itens={todos} nomes={nomes} /> : null}
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
            <li key={i.tipo} className={styles.itemCompacto}>
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

/** Os itens de um app, sem repetir o pacote em cada um (a página já é do app). */
function ItensDoApp({ itens, rotulo, onMudou }: { itens: DetalheDoApp['aprendido']; rotulo: string; onMudou?: () => void }) {
  const titulos = titulosDaLista(itens);
  return (
    <ul className={styles.lista} aria-label={rotulo}>
      {itens.map((e) => (
        <ItemDoLivro
          key={chaveDoItem(e)}
          entrada={e}
          titulo={titulos.get(e)}
          ocultarApp
          acoes={onMudou ? acoesDoItem(e) : []}
          onMudou={onMudou ?? (() => undefined)}
          uso={e.uso ? { rotulo: rotuloDoUso(e.uso.camada), porque: e.uso.porque } : undefined}
          extra={e.absorvida_em ? <p className={styles.secaoLead}>Absorvido em {e.absorvida_em}</p> : null}
        />
      ))}
    </ul>
  );
}

function ListaDoApp({ titulo, lead, itens, vazio }: { titulo: string; lead: string; itens: DetalheDoApp['aprendido']; vazio: string }) {
  return (
    <section className={styles.secao} aria-label={titulo}>
      <h3 className={styles.secaoTitulo}>{titulo} ({formatInt(itens.length)})</h3>
      <p className={styles.secaoLead}>{lead}</p>
      {itens.length === 0 ? <p className={styles.secaoLead}>{vazio}</p> : <ItensDoApp itens={itens} rotulo={titulo} />}
    </section>
  );
}

/**
 * O aprendido do app no nível Capability da hierarquia: um bloco recolhível por capability, com quantos itens tem e a
 * saúde deles no resumo; abre sozinho o bloco que tem item pedindo atenção. O fluxo (comando inteiro) e o item sem
 * capability conhecida ficam em blocos próprios, por último.
 */
function AprendidoPorCapability({ itens, onMudou }: { itens: DetalheDoApp['aprendido']; onMudou: () => void }) {
  const grupos = gruposDoAprendido(itens, rotuloDoKind);
  return (
    <section className={styles.secao} aria-label="Aprendido">
      <h3 className={styles.secaoTitulo}>Aprendido ({formatInt(itens.length)})</h3>
      <p className={styles.secaoLead}>
        O que o sistema aprendeu deste app, por capacidade, com o estado, a saúde e a decisão da pessoa.
      </p>
      {itens.length === 0 ? <EmptyState icon={AppWindow} compact title="Nada aprendido para este app" /> : (
        <div className={styles.grupos} data-grupos>
          {grupos.map((g) => {
            const atencao = quantosPedemAtencao(g.itens);
            return (
              <Disclosure
                key={g.chave}
                className={styles.grupo}
                defaultOpen={atencao > 0 || grupos.length === 1}
                summary={(
                  <span className={g.ehCapability ? styles.mono : undefined} title={g.codigo ?? undefined}
                    data-capability={g.codigo ?? undefined}>{g.titulo}</span>
                )}
                meta={(
                  <span className={styles.grupoMeta}>
                    <span>{formatInt(g.itens.length)} {g.itens.length === 1 ? 'item' : 'itens'}</span>
                    {atencao > 0 ? <Badge tone="warning" size="sm">{textoDeAtencao(atencao)}</Badge> : null}
                    {contarPorRotulo(g.itens).length > 0 ? <ChipsDeSaude itens={g.itens} /> : null}
                  </span>
                )}
              >
                {() => <ItensDoApp itens={g.itens} rotulo={`Aprendido: ${g.titulo}`} onMudou={onMudou} />}
              </Disclosure>
            );
          })}
        </div>
      )}
    </section>
  );
}

/**
 * Lições e telas neste app (30.20, §8.10): o modo que VALE aqui e se ele é do app (override no config) ou do global.
 * Só leitura: o modo por app é configuração da instalação, lida ao iniciar o central; o painel diz onde mudar.
 */
function ModosNesteApp({ pacote, modos }: { pacote: string; modos: ModosDoApp | null }) {
  if (!modos) return null;
  const linhas = modosDoAppEmTexto(modos);
  return (
    <div className={styles.cartaoLinha} data-modo-do-app>
      <span className={styles.cartaoRotulo}>Lições e telas neste app</span>
      <ul className={styles.modosDoApp} aria-label="Modo de lições e telas neste app">
        {linhas.map((m) => (
          <li key={m.chave} className={styles.modoDoApp} data-modo={m.chave} data-origem={m.doApp ? 'app' : 'global'}>
            <span className={styles.modoDoAppHead}>
              <strong>{m.tipo}</strong>
              <span>{m.modo}</span>
              <Badge tone={m.doApp ? 'info' : 'neutral'} size="sm"
                title={m.doApp ? 'O config da instalação define um modo só para este app.' : 'Sem modo próprio: vale o modo global.'}>
                {m.doApp ? 'definido para este app' : 'segue o global'}
              </Badge>
            </span>
            {m.efeito ? <span className={styles.modoEfeito}>{m.efeito}</span> : null}
          </li>
        ))}
      </ul>
      <Disclosure bare summary="Como mudar o modo deste app">
        {() => (
          <div className={styles.comoMudar} data-como-mudar>
            <p>O painel só mostra o modo. Quem decide é o arquivo de configuração do central.</p>
            <ol className={styles.passosDeConfig}>
              <li>
                No computador central, abra o arquivo <strong className={styles.mono}>config/config.yaml</strong>, na pasta
                do central.
              </li>
              <li>
                Dentro de <span className={styles.mono}>aprendizado</span>, mude o valor na linha deste app. O trecho abaixo
                já traz o modo que vale hoje:
                <pre className={styles.trechoDeConfig} aria-label="Trecho do config.yaml">{trechoDeConfig(pacote, modos)}</pre>
              </li>
              <li>
                Reinicie o central: no Agendador de Tarefas do Windows, encerre e execute de novo a tarefa{' '}
                <span className={styles.mono}>farm-central</span>. O arquivo só é lido quando o central inicia.
              </li>
            </ol>
            <div className={styles.valoresDoModo}>
              {TIPOS_COM_MODO_POR_APP.map(({ chave, nome }) => (
                <div key={chave}>
                  <span className={styles.cartaoRotulo}>Valores para {nome.toLowerCase()}</span>
                  <ul>
                    {valoresDoModo(chave).map((v) => (
                      <li key={v.valor}><span className={styles.mono}>{v.valor}</span>: {v.rotulo} — {v.efeito}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
            <p>Para o app voltar a seguir o modo global, apague a linha dele.</p>
          </div>
        )}
      </Disclosure>
    </div>
  );
}

function DetalheDeUmApp({ pacote }: { pacote: string }) {
  const { dado: d, erro, carregando, carregar } = useCarga<DetalheDoApp>((s) => apiAprendizado.app(pacote, s), pacote);
  const voltar = () => useUiStore.getState().navegar({ tela: 'aprendizado', query: { aba: 'apps' } });
  const balde = pacote === NAO_RESOLVIDO;
  // A saúde de cada item vem da lista do Livro filtrada pelo app: as linhas de /apps/{pacote} chegam sem `saude`.
  const livro = useCarga((s) => apiAprendizado.livro({ app: pacote }, s), `livro:${pacote}`);
  const doLivro = useMemo(() => (livro.dado && Array.isArray(livro.dado.itens) ? livro.dado.itens : undefined), [livro.dado]);
  const saudePorItem = useMemo(() => new Map((doLivro ?? []).map((e) => [chaveDoItem(e), e.saude ?? null] as const)), [doLivro]);
  const aprendido = useMemo(() => (d?.aprendido ?? []).map((e) => (e.saude ? e : { ...e, saude: saudePorItem.get(chaveDoItem(e)) ?? null })), [d, saudePorItem]);
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
          <div className={styles.painelDoApp}>
            <div className={styles.cartaoLinha} data-uso-do-app>
              <span className={styles.cartaoRotulo}>Como é usado</span>
              <UsoEmChips uso={d.app.uso} />
            </div>
            {!balde ? <ModosNesteApp pacote={pacote} modos={d.app.modos_do_app} /> : null}
            <div className={styles.cartaoLinha} data-saude-do-app>
              <span className={styles.cartaoRotulo}>Saúde do aprendido</span>
              {doLivro ? <ChipsDeSaude itens={doLivro} /> : livro.erro
                ? <LoadErrorBanner error={livro.erro} onRetry={() => void livro.carregar()} /> : <span>Carregando…</span>}
            </div>
          </div>
          {/* Do que pede ação ao que é referência: atenção, o aprendido, o que falha; o declarado e o absorvido depois. */}
          {doLivro ? <FilaDeAtencao itens={doLivro} /> : null}
          <AprendidoPorCapability itens={aprendido} onMudou={() => { void carregar(); void livro.carregar(); }} />
          <FalhasDoApp pacote={pacote} />
          {!balde ? <Declarado d={d} /> : null}
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
