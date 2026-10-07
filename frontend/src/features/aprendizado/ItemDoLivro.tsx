import { Zap } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { api } from '../../api/client';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { Checkbox } from '../../components/Field';
import { SeloEmProva } from '../../components/SeloEmProva';
import { SeloEmUsoReal, SeloNascidoDeProva } from '../../components/SeloNascidoDeProva';
import { StatusBadge } from '../../components/StatusBadge';
import { explicacaoEmProva, textoDaEsperaDaPessoa } from '../../lib/emProva';
import { cx, formatInt } from '../../lib/format';
import { saveJson } from '../../lib/storage';
import { toLoadError } from '../../lib/loadError';
import { formatDateTime, formatQuando } from '../../lib/time';
import { useUiStore } from '../../store/ui';
import { apiAprendizado } from './api';
import { DecisaoInline } from './DecisaoInline';
import { DetalheRico } from './DetalheRico';
import { motivoEmPalavras } from './aprovacaoAutomatica';
import { metaDeSaude } from './detalhe';
import { abrirApp, dicaDoApp, nomeDoApp } from './apps';
import { AppsDoItem, eMultiApp } from './AppsDoItem';
import {
  type AcaoDoItem, type DetalheDoLivro, type EntradaDoLivro, ESTADO_META, ONDE_FICAM_AS_HABILIDADES,
  ORIGEM_LABEL, porQuemDecidiu, porQueOSistemaNaoPublica, refDaHabilidade, rotuloDoDetalhe, rotuloDoKind, tituloDoItem,
} from './model';
import { assuntoDoItem } from './assunto';
import { acaoDePublicarOFato, fatoDaOperacaoDe } from './fatoDaOperacao';
import { ParecerNaLinha } from './ParecerDaIA';
import { lerProvaDaCandidata, rotuloDoResultado, textoDaProva, textoDaSubstituida } from './provaDaCandidata';
import styles from './Aprendizado.module.css';

export { DecisaoInline };

export const chaveDoItem = (e: Pick<EntradaDoLivro, 'kind' | 'ref'>) => `${e.kind}:${e.ref}`;

/**
 * Aplica UMA transição e devolve o erro legível (ou `null`). O motivo nunca sai daqui para um toast ou log. Com um
 * parecer da IA pendente na linha (30.17), a decisão vai com o `review_id`: fica registrada contra o parecer que a
 * pessoa via (aceitou, se foi para o lado dele; recusou, se não).
 *
 * Habilidade vai direto pela rota das habilidades (`POST /api/skills/{id}/versions/{n}/status`): a do livro a recusa
 * com 409 `use_skills_route`, e mandar lá primeiro só gastaria uma ida. Quem decide continua sendo a pessoa da sessão
 * (a rota registra o operador); a recusa do domínio dela (comando duplicado, transição proibida) volta como a de
 * qualquer item.
 */
export async function aplicarTransicao(e: EntradaDoLivro, acao: Pick<AcaoDoItem, 'to' | 'confirmaQueFica'>,
                                       motivo: string): Promise<string | null> {
  try {
    if (acao.confirmaQueFica) {
      await apiAprendizado.confirmarQueFica(e.kind, e.ref, motivo, e.parecer?.id);
    } else if (e.kind === 'habilidade') {
      const ref = refDaHabilidade(e.ref);
      if (!ref) return `A referência "${e.ref}" não diz a versão: decida em ${ONDE_FICAM_AS_HABILIDADES}.`;
      await api.transitionSkill(ref.skillId, ref.version, { to: acao.to, reason: motivo });
    } else {
      await apiAprendizado.mudarEstado(e.kind, e.ref, acao.to, motivo, e.parecer?.id);
    }
    return null;
  } catch (err) {
    const recusa = toLoadError(err);
    return `${recusa.message} ${recusa.hint}`.trim();
  }
}

/** Abre Configuração já em Fluxos e receitas (`?aba=fluxos`), com a seção Habilidades aberta (a chave que a página
 *  lembra, como faz `infra/CriarAparelho.tsx` para Limites). */
function abrirHabilidades(): void {
  saveJson('settings.section.habilidades', true);
  useUiStore.getState().navegar({ tela: 'configuracao', query: { aba: 'fluxos' } });
}

/**
 * O aviso da habilidade: ciclo próprio, publicar é sempre de uma pessoa. Na fila, diz o que publicar faz (a mesma regra
 * do diálogo de Configuração); no catálogo, onde ficam as outras decisões. Nos dois, o caminho até o ciclo completo.
 */
export function AvisoDaHabilidade({ naFila }: { naFila: boolean }) {
  return (
    <p className={styles.secaoLead}>
      {naFila
        ? 'Habilidade tem ciclo próprio e publicar é sempre de uma pessoa: publicar aqui usa a rota das habilidades. '
          + 'A versão publicada desta habilidade, se houver, é substituída na mesma operação; a publicação é recusada se '
          + 'um fluxo ativo ou outra habilidade publicada tiver o mesmo comando. '
        : 'Habilidade tem ciclo próprio e publicar é sempre de uma pessoa: publicar, recolher e desabilitar ficam em '}
      <button type="button" className={styles.linkBtn} onClick={abrirHabilidades}>
        {naFila ? `Ciclo completo em ${ONDE_FICAM_AS_HABILIDADES}` : ONDE_FICAM_AS_HABILIDADES}
      </button>
      {naFila ? '' : '.'}
    </p>
  );
}

function DetalheDoItem({ entrada, onMudou }: { entrada: EntradaDoLivro; onMudou?: () => void }) {
  const [detalhe, setDetalhe] = useState<DetalheDoLivro | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  // Um gesto no próprio detalhe (a evidência inválida, 30.23; o parecer da IA, 30.17) relê o detalhe e avisa a lista,
  // que pode tirar o item da fila.
  const [leitura, setLeitura] = useState(0);
  useEffect(() => {
    const ctl = new AbortController();
    apiAprendizado.detalhe(entrada.kind, entrada.ref, ctl.signal)
      .then((d) => setDetalhe(d))
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setErro(toLoadError(e).message);
      });
    return () => ctl.abort();
  }, [entrada.kind, entrada.ref, leitura]);
  if (erro) return <p className={styles.erroInline}>{erro}</p>;
  if (!detalhe) return <p className={styles.secaoLead}>Carregando o detalhe do item…</p>;
  return <DetalheRico detalhe={detalhe} onMudou={() => { setLeitura((n) => n + 1); onMudou?.(); }} />;
}

interface ItemDoLivroProps {
  entrada: EntradaDoLivro;
  /** As ações desta lista (a fila oferece aprovar e rejeitar; "Revisar", rebaixar; o catálogo, todas as da pessoa). */
  acoes: readonly AcaoDoItem[];
  selecionado?: boolean;
  onSelecionar?: (sim: boolean) => void;
  onMudou: () => void;
  extra?: ReactNode;
  /** Abre o detalhe já na primeira vista (o link de uma relação leva direto ao item). */
  abrirDetalhe?: boolean;
  /** Dentro da página de um app, repetir o pacote em cada item só ocupa a linha. */
  ocultarApp?: boolean;
  /** Como o item é usado hoje (camada de uso), quando a lista sabe. */
  uso?: { rotulo: string; porque?: string | null };
  /** O título já sem repetição na lista (`titulosDaLista`); sem ele, o do item (`tituloDoItem`). */
  titulo?: string;
  /** 31.209: com ele, o assunto da lição vira botão que filtra o Livro por esse assunto; sem ele, o assunto aparece só como texto. */
  onFiltrarAssunto?: (assunto: string) => void;
  /**
   * 30.66: o motivo de "publicado antes da regra de aprovação" que o cabeçalho da lista já disse uma vez (em Revisar,
   * "tem efeito externo"). O item com esse mesmo motivo não repete a frase; o que tiver outro motivo segue com a dele.
   */
  avisoNoCabecalho?: string | null;
}

/** Uma linha do livro: o que é, em que estado, por que espera o dono e o que a pessoa pode fazer. */
export function ItemDoLivro({ entrada: e, acoes: acoesDaLista, selecionado, onSelecionar, onMudou, extra, abrirDetalhe, ocultarApp, uso, titulo: tituloDaLista, onFiltrarAssunto, avisoNoCabecalho = null }: ItemDoLivroProps) {
  const assunto = assuntoDoItem(e);
  // 31.214: a candidata que veio de fato de operação (o `source_kind` na entrada) tem o Publicar na linha; sem ele, o detalhe a reconhece pelo conteúdo.
  const fato = fatoDaOperacaoDe(e);
  const publicarOFato = acaoDePublicarOFato(e);
  const acoes = publicarOFato ? [...acoesDaLista, publicarOFato] : acoesDaLista;
  const [aberta, setAberta] = useState<AcaoDoItem | null>(null);
  const titulo = tituloDaLista ?? tituloDoItem(e);
  const porQue = porQueOSistemaNaoPublica(e);
  // Publicado e ainda "espera o dono": é item anterior à regra de aprovação (efeito externo publicado antes do D1).
  // Já decidido por uma pessoa (confirmou que fica, religou: `em_revisar` falso, 30.24), o aviso sai.
  const publicadoAntes = e.state === 'published' && !!e.por_que_nao_publica?.espera_o_dono;
  const anterior = publicadoAntes && e.em_revisar !== false;
  const esperaDoItem = e.por_que_nao_publica?.espera_o_dono && !(publicadoAntes && e.em_revisar === false) ? porQue : null;
  const espera = anterior && esperaDoItem !== null && esperaDoItem === avisoNoCabecalho ? null : esperaDoItem;
  const naoPublica = e.por_que_nao_publica?.espera_o_dono ? null : porQue;
  // 31.270: a receita candidata diz a prova (concordâncias seguidas de N, última consulta, a ativa que ela quer substituir); a contagem
  // "sombra a/b" do `detail` vira essa linha e não se repete como "na sombra, concordou…".
  const prova = lerProvaDaCandidata(e);
  const detalhe = prova && /^sombra /.test(e.detail ?? '') ? null : rotuloDoDetalhe(e.detail);
  const memoria = e.kind === 'memoria';
  // O rótulo vem pronto do backend (a mesma função na lista e no detalhe): aqui só se escolhe o tom e o texto.
  const saude = metaDeSaude(e.saude?.rotulo);

  return (
    <li className={cx(styles.item, selecionado && styles.itemSelecionado)} data-item={chaveDoItem(e)}>
      <div className={styles.itemHead}>
        {onSelecionar ? (
          <Checkbox aria-label={`Selecionar ${titulo}`} checked={!!selecionado} onChange={(ev) => onSelecionar(ev.target.checked)} />
        ) : null}
        <Badge tone="neutral" size="sm">{rotuloDoKind(e.kind)}</Badge>
        {/* O título cru (a chave da etapa, o texto com o código) e a referência no Livro ("receita:108") ficam no `title`:
            é o que quem desenvolve procura (UX dos deploys 7 e 8: a referência ficava à mostra no canto do cartão). */}
        <span className={styles.itemTitulo} title={`${e.title || e.ref} · ${chaveDoItem(e)}`}>{titulo}</span>
        {e.side_effect ? <Badge tone="warning" size="sm" icon={Zap} title="Tem efeito externo (mensagem, publicação, envio…)">efeito externo</Badge> : null}
        {e.reaprendido ? (
          <Badge tone="warning" size="sm" title={`Reaprendido depois de uma evidência inválida (execução ${e.reaprendido.run_invalidada}): a aprovação é sua`}>
            reaprendido
          </Badge>
        ) : null}
        {e.state ? <StatusBadge meta={ESTADO_META[e.state]} size="sm" /> : null}
        {/* 30.85: o ensinado ainda em prova segue "Publicado", mas só vale para a persona que ensinou; o selo é o do 30.81. */}
        {e.kind === 'fluxo' ? <SeloEmProva ensinado={e.ensinado_em_prova} /> : null}
        {e.kind === 'fluxo' ? <SeloNascidoDeProva nascido={e.nascido_de_prova} /> : null}
        {e.kind === 'fluxo' && e.state !== 'disabled' ? <SeloEmUsoReal desde={e.em_uso_real_desde} /> : null}
        {saude ? <Badge tone={saude.tone} size="sm" icon={saude.icon} title={saude.description} className={styles.seloDeSaude}><span className="sr-only">Saúde: </span>{saude.label}</Badge> : null}
      </div>
      <div className={styles.itemMeta}>
        {memoria ? <span><strong>{formatInt(e.count ?? 0)} lembranças</strong> (o conteúdo fica com a persona)</span> : null}
        {/* O fluxo que atravessa apps mostra todos, também dentro da aba do app (30.33-C). */}
        {eMultiApp(e.apps) ? (
          <span>Apps: <AppsDoItem apps={e.apps} nomes={e.apps_nomes} principal={e.app} /></span>
        ) : e.app && !ocultarApp ? (
          <span>App:{' '}
            <button type="button" className={styles.linkBtn} title={dicaDoApp(e.app)} onClick={() => abrirApp(e.app as string)}>
              {nomeDoApp(e.app, e.app_nome) !== e.app ? nomeDoApp(e.app, e.app_nome) : <span className={styles.mono}>{e.app}</span>}
            </button>
          </span>
        ) : null}
        {assunto ? (
          <span data-assunto>Assunto:{' '}
            {onFiltrarAssunto ? (
              <button type="button" className={styles.linkBtn} title="Mostrar só as lições deste assunto" onClick={() => onFiltrarAssunto(assunto)}>{assunto}</button>
            ) : assunto}
          </span>
        ) : null}
        {fato ? <span data-fato-da-operacao>Fato da pesquisa de uma operação · só uma pessoa publica</span> : null}
        <span>{ORIGEM_LABEL[e.origin] ?? e.origin}</span>
        {/* Na receita os números são as reproduções; "Evidência" ficava contra a seção "Evidência registrada" do detalhe. */}
        {memoria ? null : e.kind === 'receita'
          ? e.evidence.for + e.evidence.against === 0 ? null : <span>Reproduções: {formatInt(e.evidence.for)} deram certo · {formatInt(e.evidence.against)} falharam</span>
          : <span>Evidência: {e.evidence.for} a favor · {e.evidence.against} contra</span>}
        {typeof e.uses === 'number' && e.uses > 0 ? <span>Usos: {formatInt(e.uses)}</span> : null}
        {e.last_used_at ? <span title={formatDateTime(e.last_used_at)}>Último uso: {formatQuando(e.last_used_at)}</span>
          : e.uses === 0 && !memoria ? <span>Nunca usado</span> : null}
        {detalhe ? <span>{detalhe}</span> : null}
        {uso ? <span title={uso.porque ?? undefined}>Uso: {uso.rotulo}</span> : null}
      </div>
      {prova ? (
        <p className={styles.notaDoItem} data-prova-da-candidata>
          <strong>Prova da candidata:</strong> {textoDaProva(prova)}.
          {prova.consulta ? (
            <span data-ultima-consulta>
              {' '}Última consulta{prova.consulta.em ? <> <span title={formatDateTime(prova.consulta.em)}>{formatQuando(prova.consulta.em)}</span></> : null}
              : {rotuloDoResultado(prova.consulta.resultado)}.
            </span>
          ) : null}
          {prova.substitui ? <span data-substitui> {textoDaSubstituida(prova.substitui)}</span> : null}
        </p>
      ) : null}
      {espera ? (
        <p className={styles.avisoDoItem}>
          {anterior ? `Publicado antes da regra de aprovação (${espera}): vale revisar.` : `Espera o dono: ${espera}`}
        </p>
      ) : null}
      {naoPublica ? <p className={styles.notaDoItem}>O sistema não publica sozinho: {naoPublica}</p> : null}
      {/* 30.81: o ensinado que a prova automática passou à pessoa. Até ela decidir, só vale para a persona que ensinou. */}
      {e.kind === 'fluxo' && e.espera_a_pessoa ? (
        <p className={styles.avisoDoItem} title={`motivo: ${e.espera_a_pessoa}`}>
          Ensinado, ainda em prova: o uso fica restrito até ela passar (só a persona que ensinou; sem persona na gravação,
          nenhum aparelho). {textoDaEsperaDaPessoa(e.espera_a_pessoa)} “Confirmar que fica” libera o fluxo para quem estiver no escopo.
        </p>
      ) : null}
      {/* Em prova sem a pessoa na jogada (a prova automática ainda decide): a nota curta; com `espera_a_pessoa` vale a de cima. */}
      {e.kind === 'fluxo' && e.ensinado_em_prova && !e.espera_a_pessoa ? (
        <p className={styles.notaDoItem}>{explicacaoEmProva(e.ensinado_em_prova)}</p>
      ) : null}
      {e.confirmado ? (
        <p className={styles.notaDoItem}>
          Confirmado que fica {porQuemDecidiu(e.confirmado.por)},{' '}
          <span title={formatDateTime(e.confirmado.em)}>{formatQuando(e.confirmado.em)}</span>
          {e.confirmado.motivo ? <>: <span title={e.confirmado.motivo}>{motivoEmPalavras(e.confirmado.motivo)}</span></> : ''}. Volta para Revisar se aparecer evidência contrária.
        </p>
      ) : null}
      {e.confirmacao_contestada && e.em_revisar ? (
        <p className={styles.notaDoItem}>
          Voltou para revisar: confirmado que fica {porQuemDecidiu(e.confirmacao_contestada.por)},{' '}
          <span title={formatDateTime(e.confirmacao_contestada.em)}>{formatQuando(e.confirmacao_contestada.em)}</span>,
          e depois chegou evidência contrária (veja em Detalhes, evidência e trilha).
        </p>
      ) : null}
      {e.parecer ? <ParecerNaLinha p={e.parecer} /> : null}
      {extra}
      {acoes.length > 0 && !aberta ? (
        <div className={styles.itemAcoes}>
          {acoes.map((a) => (
            <Button key={a.to} size="sm" variant={a.perigo ? 'dangerGhost' : a.to === 'published' || a.to === 'validated' ? 'primary' : 'secondary'}
                    title={a.efeito} onClick={() => setAberta(a)}>
              {a.label}
            </Button>
          ))}
        </div>
      ) : null}
      {aberta ? (
        <DecisaoInline
          acao={aberta}
          resumo={aberta.efeito}
          motivoOpcional={aberta.confirmaQueFica}
          onCancelar={() => setAberta(null)}
          onConfirmar={async (motivo) => {
            const falha = await aplicarTransicao(e, aberta, motivo);
            if (!falha) {
              setAberta(null);
              onMudou();
            }
            return falha;
          }}
        />
      ) : null}
      {!memoria ? (
        <Disclosure bare summary="Detalhes, evidência e trilha" defaultOpen={abrirDetalhe}>
          {() => <DetalheDoItem entrada={e} onMudou={onMudou} />}
        </Disclosure>
      ) : null}
    </li>
  );
}
