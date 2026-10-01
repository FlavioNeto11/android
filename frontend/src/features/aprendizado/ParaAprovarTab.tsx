import { CheckCheck, History, Inbox, ShieldAlert } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
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
import {
  type AcaoDoItem, type EntradaDoLivro, ONDE_FICAM_AS_HABILIDADES, acaoDeAprovarNaFila, acoesNaFila, ordenarPendentes,
} from './model';
import styles from './Aprendizado.module.css';

/** "Revisar": o legado ativo com efeito só pode ser desligado pela pessoa (published → disabled). Na tela, "desligar";
 *  o nome interno da transição ("rebaixar") era jargão. */
const REBAIXAR: AcaoDoItem = { to: 'disabled', label: 'Desligar', confirmar: 'Confirmar desligamento', perigo: true };

interface Leitura {
  itens: EntradaDoLivro[] | null;
  erro: LoadError | null;
}

const VAZIA: Leitura = { itens: null, erro: null };

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
  const [lote, setLote] = useState<'aprovar' | 'rebaixar' | null>(null);

  const carregar = useCallback(async () => {
    const [p, r] = await Promise.allSettled([apiAprendizado.pendentes(), apiAprendizado.revisar()]);
    setFila((antes) => (p.status === 'fulfilled'
      ? { itens: ordenarPendentes(Array.isArray(p.value?.itens) ? p.value.itens : []), erro: null }
      : { itens: antes.itens, erro: toLoadError(p.reason) }));
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
          ? `${e.title}: decida em ${ONDE_FICAM_AS_HABILIDADES}`
          : `${e.title}: não há o que aprovar neste estado`);
        continue;
      }
      const falha = await aplicarTransicao(e, acao, motivo);
      if (falha) falhas.push(`${e.title}: ${falha}`);
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
          Itens com efeito fora do sistema (mensagem, publicação) ou com texto de pessoa esperam a sua aprovação.{' '}
          <a className={styles.linkAlvo} href={hashDe('pendencias')}>Ver todas as suas pendências</a>
        </p>
        <Disclosure summary="Saiba mais" bare>
          <p className={styles.secaoLead}>
            O sistema publica sozinho só o que não tem efeito externo e já se repetiu com sucesso. O que tem efeito ou
            texto escrito por uma pessoa para aqui, já validado, aguardando você. Quando um item publicado passa a
            falhar, o sistema o desliga sozinho.
          </p>
        </Disclosure>
        {fila.erro ? <LoadErrorBanner error={fila.erro} onRetry={() => void carregar()} /> : null}
        {itensFila.length > 0 ? (
          <div className={styles.toolbar}>
            <span className={styles.secaoLead}>{escolhidosFila.length} selecionado(s)</span>
            <div className={styles.toolbarFim}>
              <Button size="sm" variant="ghost" onClick={() => setSelFila(new Set(itensFila.map(chaveDoItem)))}>Selecionar todos</Button>
              <Button size="sm" variant="primary" icon={CheckCheck}
                      disabledReason={escolhidosFila.length === 0 ? 'Selecione ao menos um item.' : null}
                      onClick={() => setLote('aprovar')}>
                Aprovar selecionados ({escolhidosFila.length})
              </Button>
            </div>
          </div>
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
                acoes={acoesNaFila(e)}
                selecionado={selFila.has(chaveDoItem(e))}
                onSelecionar={(sim) => alternar(setSelFila)(e, sim)}
                onMudou={() => void carregar()}
                extra={e.kind === 'habilidade' ? <AvisoDaHabilidade naFila /> : null}
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
              <Button size="sm" variant="dangerGhost"
                      disabledReason={escolhidosLegado.length === 0 ? 'Selecione ao menos um item.' : null}
                      onClick={() => setLote('rebaixar')}>
                Desligar selecionados ({escolhidosLegado.length})
              </Button>
            </div>
          </div>
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
