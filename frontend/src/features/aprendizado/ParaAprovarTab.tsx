import { Bot, CheckCheck, History, Inbox, ShieldAlert } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toApiError } from '../../api/client';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { hashDe } from '../../lib/rotas';
import { LoadErrorBanner, LoadErrorState, toLoadError, type LoadError } from '../../lib/loadError';
import { toast } from '../../store/toasts';
import { usePendenciasStore } from '../pendencias/store';
import { apiAprendizado } from './api';
import { useContagemDoAprendizado } from './contagem';
import { AvisoDaHabilidade, DecisaoInline, ItemDoLivro, aplicarTransicao, chaveDoItem } from './ItemDoLivro';
import { ResumoParaDecidir } from './ResumoParaDecidir';
import {
  type AcaoDoItem, type EntradaDoLivro, ONDE_FICAM_AS_HABILIDADES, acaoDeAprovarNaFila, acoesNaFila, ordenarPendentes,
  tituloDoItem, titulosDaLista,
} from './model';
import { type ModoDoCurador, efeitoDoAceite, textoDaRecusa } from './parecer';
import styles from './Aprendizado.module.css';

/** "Revisar": o legado ativo com efeito só pode ser desligado pela pessoa (published → disabled). Na tela, "desligar";
 *  o nome interno da transição ("rebaixar") era jargão. */
const REBAIXAR: AcaoDoItem = { to: 'disabled', label: 'Desligar', confirmar: 'Confirmar desligamento', perigo: true };

interface Leitura {
  itens: EntradaDoLivro[] | null;
  erro: LoadError | null;
}

const VAZIA: Leitura = { itens: null, erro: null };

type Lote = 'aprovar' | 'rebaixar' | 'pareceres-fila' | 'pareceres-legado';

/** O parecer da linha entra no aceite em lote? O backend já disse (`recusa_no_lote` nulo: classe B, real, atual). */
const entraNoLote = (e: EntradaDoLivro): boolean => !!e.parecer && !e.parecer.recusa_no_lote;

/** O botão "Aceitar pareceres" de uma seção: só com o curador ligado e algum parecer à vista. */
function AceitarPareceres({ modo, itens, escolhidos, onAbrir }: {
  modo: ModoDoCurador | null; itens: readonly EntradaDoLivro[]; escolhidos: readonly EntradaDoLivro[]; onAbrir: () => void;
}) {
  if (modo !== 'on' || !itens.some((e) => e.parecer)) return null;
  const n = escolhidos.filter(entraNoLote).length;
  return (
    <Button size="sm" variant="secondary" icon={Bot}
            disabledReason={n === 0 ? 'Selecione itens com parecer da IA da classe B (a C se decide item a item).' : null}
            onClick={onAbrir}>
      Aceitar pareceres da IA ({n})
    </Button>
  );
}

/**
 * A fila do D1 (ADR-054): o que o sistema NÃO publica sozinho — tem efeito externo ou texto de pessoa — e espera o
 * dono, com a evidência ao lado e a aprovação em lote. A habilidade validada também espera aqui (publicar é sempre
 * de uma pessoa) e se decide pela rota das habilidades (`acoesNaFila`, `aplicarTransicao`). Embaixo, "Revisar":
 * receitas e fluxos já ativos com efeito, anteriores ao D1, que continuam valendo até o dono decidir (desvio
 * consciente do ADR-054).
 */
export function ParaAprovarTab() {
  const [fila, setFila] = useState<Leitura>(VAZIA);
  const [legado, setLegado] = useState<Leitura>(VAZIA);
  const [selFila, setSelFila] = useState<Set<string>>(() => new Set());
  const [selLegado, setSelLegado] = useState<Set<string>>(() => new Set());
  const [lote, setLote] = useState<Lote | null>(null);
  const [modo, setModo] = useState<ModoDoCurador | null>(null);

  const carregar = useCallback(async () => {
    const [p, r] = await Promise.allSettled([apiAprendizado.pendentes(), apiAprendizado.revisar()]);
    setFila((antes) => (p.status === 'fulfilled'
      ? { itens: ordenarPendentes(Array.isArray(p.value?.itens) ? p.value.itens : []), erro: null }
      : { itens: antes.itens, erro: toLoadError(p.reason) }));
    if (p.status === 'fulfilled') setModo(p.value?.curador?.modo ?? null);
    setLegado((antes) => (r.status === 'fulfilled'
      ? { itens: Array.isArray(r.value?.itens) ? r.value.itens : [], erro: null }
      : { itens: antes.itens, erro: toLoadError(r.reason) }));
    void useContagemDoAprendizado.getState().atualizar();
    void usePendenciasStore.getState().atualizar();
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  // A seleção só guarda o que ainda está na lista (o item aprovado sai da fila).
  const itensFila = useMemo(() => fila.itens ?? [], [fila.itens]);
  const itensLegado = useMemo(() => legado.itens ?? [], [legado.itens]);
  const escolhidosFila = itensFila.filter((e) => selFila.has(chaveDoItem(e)));
  const escolhidosLegado = itensLegado.filter((e) => selLegado.has(chaveDoItem(e)));
  // Os títulos sem repetição de cada lista (P4 do deploy 3); os avisos do lote usam os mesmos, para achar o item.
  const titulos = useMemo(() => new Map([...titulosDaLista(itensFila), ...titulosDaLista(itensLegado)]),
                          [itensFila, itensLegado]);
  const tituloDe = (e: EntradaDoLivro) => titulos.get(e) ?? tituloDoItem(e);

  const alternar = (set: typeof setSelFila) => (e: EntradaDoLivro, sim: boolean) =>
    set((antes) => {
      const prox = new Set(antes);
      if (sim) prox.add(chaveDoItem(e));
      else prox.delete(chaveDoItem(e));
      return prox;
    });

  /** Em lote = uma transição por item, EM SEQUÊNCIA (o CAS de cada uma é independente; não há rota de lote). */
  const aplicarEmLote = async (itens: EntradaDoLivro[], acaoDe: (e: EntradaDoLivro) => AcaoDoItem | null,
                               motivo: string): Promise<string | null> => {
    let ok = 0;
    const falhas: string[] = [];
    for (const e of itens) {
      const acao = acaoDe(e);
      if (!acao) {
        falhas.push(e.kind === 'habilidade'
          ? `${tituloDe(e)}: decida em ${ONDE_FICAM_AS_HABILIDADES}`
          : `${tituloDe(e)}: não há o que aprovar neste estado`);
        continue;
      }
      const falha = await aplicarTransicao(e, acao, motivo);
      if (falha) falhas.push(`${tituloDe(e)}: ${falha}`);
      else ok += 1;
    }
    toast({
      tone: falhas.length > 0 ? 'warning' : 'success',
      title: `${ok} de ${itens.length} item(ns) decidido(s)`,
      details: falhas.length > 0 ? falhas : null,
    });
    setSelFila(new Set());
    setSelLegado(new Set());
    setLote(null);
    await carregar();
    return null;
  };

  /**
   * O aceite de pareceres em lote (30.17): um gesto por item, EM SEQUÊNCIA, com o mesmo motivo, só nos que o backend
   * deixa entrar (classe B, parecer real e atual). Os selecionados que ficam fora vão no aviso, com a razão; o backend
   * confere de novo cada um (a classe de agora pode ter endurecido).
   */
  const aceitarPareceres = async (itens: EntradaDoLivro[], motivo: string): Promise<string | null> => {
    let ok = 0;
    const falhas: string[] = [];
    const feitos: string[] = [];
    for (const e of itens) {
      const p = e.parecer;
      if (!p || p.recusa_no_lote) {
        falhas.push(`${tituloDe(e)}: ${textoDaRecusa(p?.recusa_no_lote) ?? 'sem parecer da IA'}`);
        continue;
      }
      try {
        await apiAprendizado.responderParecer(e.kind, e.ref, p.id, { resposta: 'aceitar', motivo, em_lote: true });
        ok += 1;
        feitos.push(`${tituloDe(e)}: ${efeitoDoAceite(p.acao)}`);
      } catch (err) {
        const x = toApiError(err);
        falhas.push(`${tituloDe(e)}: ${textoDaRecusa(x.code) === x.code ? toLoadError(err).message : textoDaRecusa(x.code)}`);
      }
    }
    toast({
      tone: falhas.length > 0 ? 'warning' : 'success',
      title: `${ok} parecer(es) aceito(s) de ${itens.length} item(ns) selecionado(s)`,
      details: falhas.length + feitos.length > 0 ? [...falhas, ...feitos] : null,
    });
    setSelFila(new Set());
    setSelLegado(new Set());
    setLote(null);
    await carregar();
    return null;
  };

  if (fila.itens === null && legado.itens === null) {
    if (fila.erro && legado.erro) return <LoadErrorState what="a fila Para aprovar" error={fila.erro} onRetry={() => void carregar()} />;
    return (
      <LoadingRegion label="Carregando a fila Para aprovar…" className={styles.secao}>
        <Skeleton height={72} radius={8} />
        <Skeleton height={72} radius={8} />
      </LoadingRegion>
    );
  }

  return (
    <>
      <section className={styles.secao} aria-labelledby="aprendizado-fila">
        <h2 id="aprendizado-fila" className={styles.secaoTitulo}><Inbox size={16} aria-hidden /> Para aprovar</h2>
        <p className={styles.secaoLead}>
          Itens com efeito fora do sistema (mensagem, publicação), com texto de pessoa ou reaprendidos depois de uma
          evidência inválida esperam a sua aprovação.{' '}
          <a className={styles.linkAlvo} href={hashDe('pendencias')}>Ver todas as suas pendências</a>
        </p>
        <Disclosure summary="Saiba mais" bare>
          <p className={styles.secaoLead}>
            O sistema publica sozinho só o que não tem efeito externo e já se repetiu com sucesso. O que tem efeito ou
            texto escrito por uma pessoa para aqui, já validado, aguardando você. O mesmo vale para o que foi
            reaprendido depois de uma evidência inválida (uma execução que terminou como sucesso sem comprovar o que
            fez): outra execução real ensinou de novo, e a decisão de voltar a usar é sua. Quando um item publicado
            passa a falhar, o sistema o desliga sozinho.
          </p>
          {modo === 'on' ? (
            <p className={styles.secaoLead}>
              Com o curador ligado, a IA dá um parecer sobre cada item. Ela nunca decide: na classe B você pode aceitar
              vários pareceres de uma vez; na C (envio, conta, sessão), decida um item de cada vez, pelo detalhe.
            </p>
          ) : null}
        </Disclosure>
        {fila.erro ? <LoadErrorBanner error={fila.erro} onRetry={() => void carregar()} /> : null}
        {itensFila.length > 0 ? (
          <div className={styles.toolbar}>
            <span className={styles.secaoLead}>{escolhidosFila.length} selecionado(s)</span>
            <div className={styles.toolbarFim}>
              <Button size="sm" variant="ghost" onClick={() => setSelFila(new Set(itensFila.map(chaveDoItem)))}>Selecionar todos</Button>
              <AceitarPareceres modo={modo} itens={itensFila} escolhidos={escolhidosFila}
                                onAbrir={() => setLote('pareceres-fila')} />
              <Button size="sm" variant="primary" icon={CheckCheck}
                      disabledReason={escolhidosFila.length === 0 ? 'Selecione ao menos um item.' : null}
                      onClick={() => setLote('aprovar')}>
                Aprovar selecionados ({escolhidosFila.length})
              </Button>
            </div>
          </div>
        ) : null}
        {lote === 'pareceres-fila' && escolhidosFila.some(entraNoLote) ? (
          <DecisaoInline
            rotulo="Motivo do aceite em lote"
            dica="Vale para cada parecer aceito: fica na trilha de cada item e no registro do parecer, com o seu nome."
            acao={{ confirmar: `Aceitar ${escolhidosFila.filter(entraNoLote).length} parecer(es)`, perigo: false }}
            onCancelar={() => setLote(null)}
            onConfirmar={(motivo) => aceitarPareceres(escolhidosFila, motivo)}
          />
        ) : null}
        {lote === 'aprovar' && escolhidosFila.length > 0 ? (
          <DecisaoInline
            rotulo="Motivo da aprovação em lote"
            acao={{ confirmar: `Confirmar aprovação de ${escolhidosFila.length}`, perigo: false }}
            onCancelar={() => setLote(null)}
            onConfirmar={(motivo) => aplicarEmLote(escolhidosFila, acaoDeAprovarNaFila, motivo)}
          />
        ) : null}
        {fila.itens !== null && itensFila.length === 0 ? (
          <EmptyState icon={Inbox} compact title="Nada aguardando você">
            Quando o sistema validar algo com efeito externo, ou uma nota sua virar candidata, aparece aqui.
          </EmptyState>
        ) : (
          <ul className={styles.lista} aria-label="Itens para aprovar">
            {itensFila.map((e) => (
              <ItemDoLivro
                key={chaveDoItem(e)}
                entrada={e}
                titulo={titulos.get(e)}
                acoes={acoesNaFila(e)}
                selecionado={selFila.has(chaveDoItem(e))}
                onSelecionar={(sim) => alternar(setSelFila)(e, sim)}
                onMudou={() => void carregar()}
                extra={e.kind === 'habilidade' ? <AvisoDaHabilidade naFila /> : <ResumoParaDecidir entrada={e} />}
              />
            ))}
          </ul>
        )}
      </section>

      <section className={styles.secao} aria-labelledby="aprendizado-revisar">
        <h2 id="aprendizado-revisar" className={styles.secaoTitulo}><History size={16} aria-hidden /> Revisar</h2>
        <Banner tone="warning" icon={ShieldAlert} compact role="note">
          Itens antigos com efeito externo, ainda ativos.
        </Banner>
        <Disclosure summary="Saiba mais" bare>
          <p className={styles.secaoLead}>
            São receitas e fluxos com efeito externo que já estavam ativos antes desta aprovação existir. Eles continuam
            valendo como antes até você decidir. Desligar um item o tira de uso (a decisão fica registrada) e a automação
            volta a pedir a IA nesses passos.
          </p>
        </Disclosure>
        {legado.erro ? <LoadErrorBanner error={legado.erro} onRetry={() => void carregar()} /> : null}
        {itensLegado.length > 0 ? (
          <div className={styles.toolbar}>
            <span className={styles.secaoLead}>{escolhidosLegado.length} selecionado(s)</span>
            <div className={styles.toolbarFim}>
              <Button size="sm" variant="ghost" onClick={() => setSelLegado(new Set(itensLegado.map(chaveDoItem)))}>Selecionar todos</Button>
              <AceitarPareceres modo={modo} itens={itensLegado} escolhidos={escolhidosLegado}
                                onAbrir={() => setLote('pareceres-legado')} />
              <Button size="sm" variant="dangerGhost"
                      disabledReason={escolhidosLegado.length === 0 ? 'Selecione ao menos um item.' : null}
                      onClick={() => setLote('rebaixar')}>
                Desligar selecionados ({escolhidosLegado.length})
              </Button>
            </div>
          </div>
        ) : null}
        {lote === 'pareceres-legado' && escolhidosLegado.some(entraNoLote) ? (
          <DecisaoInline
            rotulo="Motivo do aceite em lote"
            dica="Vale para cada parecer aceito: fica na trilha de cada item e no registro do parecer, com o seu nome."
            acao={{ confirmar: `Aceitar ${escolhidosLegado.filter(entraNoLote).length} parecer(es)`, perigo: false }}
            onCancelar={() => setLote(null)}
            onConfirmar={(motivo) => aceitarPareceres(escolhidosLegado, motivo)}
          />
        ) : null}
        {lote === 'rebaixar' && escolhidosLegado.length > 0 ? (
          <DecisaoInline
            rotulo="Motivo do desligamento em lote"
            acao={{ confirmar: `Confirmar desligamento de ${escolhidosLegado.length}`, perigo: true }}
            onCancelar={() => setLote(null)}
            onConfirmar={(motivo) => aplicarEmLote(escolhidosLegado, () => REBAIXAR, motivo)}
          />
        ) : null}
        {legado.itens !== null && itensLegado.length === 0 ? (
          <EmptyState icon={History} compact title="Nenhum legado com efeito a revisar" />
        ) : (
          <ul className={styles.lista} aria-label="Legado a revisar">
            {itensLegado.map((e) => (
              <ItemDoLivro
                key={chaveDoItem(e)}
                entrada={e}
                titulo={titulos.get(e)}
                acoes={[REBAIXAR]}
                selecionado={selLegado.has(chaveDoItem(e))}
                onSelecionar={(sim) => alternar(setSelLegado)(e, sim)}
                onMudou={() => void carregar()}
              />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
