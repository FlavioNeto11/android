import { Bot, Eye } from 'lucide-react';
import { useState } from 'react';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { toLoadError } from '../../lib/loadError';
import { formatDateTime, formatQuando } from '../../lib/time';
import { toast } from '../../store/toasts';
import { apiAprendizado } from './api';
import {
  type DecisaoDaPlataforma, type RelatorioDaAprovacao, ROTULO_DO_GESTO, fatosDoMotivo,
} from './aprovacaoAutomatica';
import { DecisaoInline } from './DecisaoInline';
import styles from './Aprendizado.module.css';

/** Quantas decisões a seção mostra antes do "Ver todas" (as mais recentes primeiro). */
const VISIVEIS = 5;

/** Uma decisão da plataforma, com o Desligar (o desfazer, ADR-054 emenda do 30.55) enquanto o item segue vivo. */
function LinhaDaDecisao({ d, onMudou }: { d: DecisaoDaPlataforma; onMudou: () => void }) {
  const [abrindo, setAbrindo] = useState(false);
  const titulo = d.titulo ?? d.item_ref;
  const vivo = d.estado === 'published' && d.kind !== null;
  const desligar = async (motivo: string): Promise<string | null> => {
    if (!d.kind) return 'Item sem tipo conhecido: decida pelo detalhe.';
    try {
      await apiAprendizado.mudarEstado(d.kind, d.ref, 'disabled', motivo);
    } catch (err) {
      const e = toLoadError(err);
      return `${e.message} ${e.hint}`.trim();
    }
    toast({ tone: 'success', title: `${titulo}: desligado`, details: ['A plataforma não decide este item de novo.'] });
    setAbrindo(false);
    onMudou();
    return null;
  };
  return (
    <li className={styles.item} data-decisao-da-plataforma={d.item_ref}>
      <div className={styles.itemHead}>
        <span className={styles.itemTitulo}>{titulo}</span>
        <Badge tone={d.gesto === 'publicar' ? 'success' : 'info'} size="sm">{ROTULO_DO_GESTO[d.gesto]}</Badge>
        {d.estado === 'disabled' ? <Badge tone="muted" size="sm">Desligado depois</Badge> : null}
      </div>
      <div className={styles.itemMeta}>
        <span title={formatDateTime(d.em)}>{formatQuando(d.em)}</span>
        {d.regra ? <span>regra <strong>{d.regra}</strong></span> : null}
      </div>
      <p className={styles.secaoLead}><strong>Por quê:</strong> {fatosDoMotivo(d.motivo)}</p>
      {vivo && !abrindo ? (
        <div className={styles.itemAcoes}>
          <Button size="sm" variant="dangerGhost" onClick={() => setAbrindo(true)}>Desligar</Button>
        </div>
      ) : null}
      {vivo && abrindo ? (
        <DecisaoInline
          rotulo="Por que desligar?"
          dica="Desfaz a decisão da plataforma: o item sai de uso, a decisão fica na trilha com o seu nome e a plataforma não o decide de novo."
          acao={{ confirmar: 'Confirmar desligamento', perigo: true }}
          onCancelar={() => setAbrindo(false)}
          onConfirmar={desligar}
        />
      ) : null}
    </li>
  );
}

/**
 * 30.55: o que a plataforma decidiu sozinha, pela régua aprovada (app de teste, classe A ou B, evidência a favor e
 * nenhuma contra). Em `shadow`, só o que ela decidiria; em `on`, as decisões, cada uma com o Desligar. Em `off` e sem
 * decisão nenhuma, a seção some (não há o que mostrar). `tituloDe` acha o nome dos itens que ainda estão nas filas.
 */
export function DecididoPelaPlataforma({ relatorio, tituloDe, onMudou }: {
  relatorio: RelatorioDaAprovacao | null;
  tituloDe: (itemRef: string) => string;
  onMudou: () => void;
}) {
  const [todas, setTodas] = useState(false);
  if (!relatorio || (relatorio.modo === 'off' && relatorio.decididos.length === 0)) return null;
  const volta = relatorio.ultima_volta;
  const visiveis = todas ? relatorio.decididos : relatorio.decididos.slice(0, VISIVEIS);
  return (
    <section className={styles.secao} aria-labelledby="aprendizado-plataforma">
      <h2 id="aprendizado-plataforma" className={styles.secaoTitulo}><Bot size={16} aria-hidden /> Decidido pela plataforma</h2>
      {relatorio.modo === 'shadow' ? (
        <Banner tone="info" icon={Eye} compact role="note">
          {/* "Nada foi decidido" só com a lista vazia: com decisões abaixo, a frase contradiz a tela. */}
          {relatorio.decididos.length === 0
            ? 'Em observação: a plataforma só anota o que decidiria. Nada foi decidido sozinho ainda.'
            : 'Em observação agora; as decisões abaixo são de quando ela decidia sozinha.'}
        </Banner>
      ) : null}
      {relatorio.modo === 'on' ? (
        <p className={styles.secaoLead}>
          A plataforma publica ou confirma sozinha o que cumpre a régua. Se discordar de uma decisão, desligue o item.
        </p>
      ) : null}
      <Disclosure summary="Como a plataforma decide" bare>
        <p className={styles.secaoLead}>
          Ela decide só a receita ou o fluxo do app de teste (sem conta real), de classe A ou B, com ao menos uma
          evidência real a favor e nenhuma contra na versão atual, sem falha de reprodução, com a saúde sem piorar e sem
          parecer do curador pedindo para rebaixar ou descartar. O Instagram, a classe C e o app sem categoria ficam
          sempre com você. Cada decisão fica na trilha do item com a regra que a tomou; desligar desfaz, e a plataforma
          não volta a decidir aquele item.
        </p>
      </Disclosure>
      {relatorio.modo === 'shadow' && volta ? (
        volta.decidiria.length > 0 ? (
          <>
            <p className={styles.secaoLead}>
              Na última volta ({formatQuando(volta.em)}), decidiria {volta.decidiria.length} de {volta.avaliados} itens:
            </p>
            <ul aria-label="O que a plataforma decidiria" className={styles.secaoLead}>
              {volta.decidiria.map((ref) => <li key={ref}>{tituloDe(ref)}</li>)}
            </ul>
          </>
        ) : (
          <p className={styles.secaoLead}>Na última volta ({formatQuando(volta.em)}), nenhum dos {volta.avaliados} itens cumpria a régua.</p>
        )
      ) : null}
      {relatorio.modo === 'shadow' && !volta ? (
        <p className={styles.secaoLead}>A primeira volta ainda não rodou (sai pouco depois de o central iniciar).</p>
      ) : null}
      {relatorio.decididos.length > 0 ? (
        <ul className={styles.lista} aria-label="Decisões da plataforma">
          {visiveis.map((d) => <LinhaDaDecisao key={`${d.item_ref}@${d.em}`} d={d} onMudou={onMudou} />)}
        </ul>
      ) : null}
      {relatorio.decididos.length > VISIVEIS ? (
        <div className={styles.toolbar}>
          <Button size="sm" variant="ghost" onClick={() => setTodas((x) => !x)}>
            {todas ? 'Mostrar só as mais recentes' : `Ver todas as ${relatorio.decididos.length} decisões`}
          </Button>
        </div>
      ) : null}
      {relatorio.decididos.length === 0 && relatorio.modo === 'on' ? (
        <EmptyState icon={Bot} compact title="Nada decidido pela plataforma ainda">
          Quando um item do app de teste cumprir a régua, ele aparece aqui, com o motivo e o botão para desligar.
        </EmptyState>
      ) : null}
    </section>
  );
}
