import { CalendarClock, TriangleAlert, Users, X } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { toApiError } from '../../api/client';
import type { Autonomia, PedidoCorpo, PedidoPrevia, Sobreposicao } from '../../api/pedidos';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Disclosure } from '../../components/Disclosure';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { cx } from '../../lib/format';
import { uuid } from '../../lib/ids';
import { useUiStore } from '../../store/ui';
import { toast } from '../../store/toasts';
import { apiPedidos } from './api';
import { fusoDoNavegador } from './formato';
import {
  DIAS_DA_SEMANA, falaDoQuando, gatilhoDoQuando, quandoInicial, type EstadoDoQuando, type Frequencia, type ModoQuando,
} from './gatilho';
import { ROTULO_DA_AUTONOMIA, ROTULO_DA_SOBREPOSICAO, instanteCanonico, mensagemDoErro } from './modelo';
import { PreviaDoPedido } from './PreviaDoPedido';
import styles from './Pedidos.module.css';

const MODOS: readonly { id: ModoQuando; rotulo: string }[] = [
  { id: 'agora', rotulo: 'Agora' }, { id: 'horario', rotulo: 'Em um horário' }, { id: 'repetir', rotulo: 'Repetir' },
  { id: 'acompanhar', rotulo: 'Acompanhar' },
];
const FREQUENCIAS: readonly { id: Frequencia; rotulo: string }[] = [
  { id: 'HOURLY', rotulo: 'horas' }, { id: 'DAILY', rotulo: 'dias' }, { id: 'WEEKLY', rotulo: 'semanas' }, { id: 'MONTHLY', rotulo: 'meses' },
];

interface Props {
  /** O objetivo que está no Comando (já aparado). */
  comando: string;
  /** Quem faz e onde, do jeito que o Comando está agora (no Automático pergunta à sugestão; nada é criado). */
  resolverAlvos: () => Promise<PedidoCorpo['alvos']>;
  /** Por que a prévia ainda não pode ser pedida (comando curto, senha no texto…); `null`/ausente = pode. */
  impede?: string | null;
  /** Uma frase com quem fará o pedido pelo Comando de agora ("android-01", "persona Ana", "a IA escolhe"). */
  quemFaz?: string;
  onFechar: () => void;
}

/** Uma seção do formulário: título curto e os campos dela, sem moldura própria (o cartão já é a moldura). */
function Secao({ titulo, dica, children }: { titulo: string; dica?: string; children: ReactNode }) {
  return (
    <section className={styles.secao} aria-label={titulo}>
      <div className={styles.secaoCabeca}>
        <h4 className={styles.secaoTitulo}>{titulo}</h4>
        {dica ? <p className={styles.secaoDica}>{dica}</p> : null}
      </div>
      {children}
    </section>
  );
}

/**
 * Criar um pedido a partir do Comando: "Quando" + critérios + limites → PRÉVIA (sem efeito, sem custo, sem IA) → só
 * "Confirmar e criar", com o selo da prévia, cria o pedido ativo (ADR-044). Mexeu em qualquer campo: a prévia vira
 * velha e some, e a confirmação exige uma nova.
 */
export function NovoPedido({ comando, resolverAlvos, impede, quemFaz, onFechar }: Props) {
  const navegar = useUiStore((s) => s.navegar);
  const [quando, setQuando] = useState<EstadoDoQuando>(() => quandoInicial());
  const [autonomia, setAutonomia] = useState<Autonomia>('observar');
  const [titulo, setTitulo] = useState('');
  const [criterios, setCriterios] = useState('');
  const [fuso, setFuso] = useState('America/Sao_Paulo');
  const [fim, setFim] = useState('');
  const [maximo, setMaximo] = useState('');
  const [orcamento, setOrcamento] = useState('');
  const [orcamentoOcorrencia, setOrcamentoOcorrencia] = useState('');
  const [sobreposicao, setSobreposicao] = useState<'' | Sobreposicao>('');
  const [tentativas, setTentativas] = useState('');
  const [previa, setPrevia] = useState<{ dados: PedidoPrevia; corpo: PedidoCorpo } | null>(null);
  const [carregando, setCarregando] = useState<'previa' | 'criar' | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const chave = useRef<{ impressao: string; valor: string } | null>(null);

  const mudou = () => { setPrevia(null); setErro(null); };
  // O objetivo mudou no Comando: a prévia de antes era de outro texto.
  useEffect(() => { setPrevia(null); }, [comando]);
  const gatilho = gatilhoDoQuando(quando);
  const numero = (t: string): number | undefined => (t.trim() !== '' && Number.isFinite(Number(t)) ? Number(t) : undefined);
  const fusoEfetivo = fuso.trim() || 'America/Sao_Paulo';
  const relogio = fusoDoNavegador();

  const montarCorpo = async (): Promise<PedidoCorpo | null> => {
    if (!gatilho) return null;
    const alvos = await resolverAlvos();
    const lista = criterios.split('\n').map((l) => l.trim()).filter(Boolean);
    const max = numero(maximo);
    const orc = numero(orcamento);
    const orcOc = numero(orcamentoOcorrencia);
    const tent = numero(tentativas);
    return {
      objetivo: comando, alvos, autonomia, fuso: fusoEfetivo, gatilhos: [gatilho],
      ...(lista.length > 0 ? { criterios_sucesso: lista } : {}),
      ...(fim ? { fim_em: instanteCanonico(new Date(fim)) } : {}),
      ...(max !== undefined ? { max_ocorrencias: max } : {}),
      ...(orc !== undefined ? { orcamento_total_usd: orc } : {}),
      ...(orcOc !== undefined ? { orcamento_ocorrencia_usd: orcOc } : {}),
      ...(sobreposicao ? { sobreposicao } : {}),
      ...(tent !== undefined ? { max_tentativas: tent } : {}),
    };
  };

  const verPrevia = async () => {
    setCarregando('previa');
    setErro(null);
    try {
      const corpo = await montarCorpo();
      if (!corpo) { setErro('Preencha o horário de início.'); return; }
      const dados = await apiPedidos.previa({ ...corpo, proximas: 5 });
      setPrevia({ dados, corpo });
    } catch (e) {
      setPrevia(null);
      setErro(mensagemDoErro(toApiError(e)));
    } finally {
      setCarregando(null);
    }
  };

  const criar = async () => {
    if (!previa?.dados.confirmacao) return;
    setCarregando('criar');
    setErro(null);
    // Mesmo conteúdo → mesma chave (nova tentativa depois de falha de rede não duplica); outro conteúdo → chave nova.
    const impressao = JSON.stringify([previa.corpo, titulo.trim(), previa.dados.confirmacao]);
    if (chave.current?.impressao !== impressao) chave.current = { impressao, valor: uuid() };
    try {
      const novo = await apiPedidos.criar({
        ...previa.corpo, idempotency_key: chave.current.valor, confirmacao: previa.dados.confirmacao,
        ...(titulo.trim() ? { titulo: titulo.trim() } : {}),
      });
      toast({
        tone: novo.deduplicated ? 'info' : 'success',
        title: novo.deduplicated ? 'Este pedido já existia: nenhum duplicado foi criado' : 'Pedido criado',
        message: novo.titulo,
      });
      onFechar();
      navegar({ tela: 'pedidos', segmentos: [novo.id] });
    } catch (e) {
      const err = toApiError(e);
      // Selo velho ou alvos que mudaram: a prévia de antes não vale mais, e a pessoa precisa ver a nova.
      if (err.code === 'previa_desatualizada' || err.code === 'alvos_nao_confirmados') setPrevia(null);
      setErro(mensagemDoErro(err));
    } finally {
      setCarregando(null);
    }
  };

  const agendado = quando.modo === 'repetir' || quando.modo === 'acompanhar';
  const escolherModo = (modo: ModoQuando) => {
    mudou();
    // "Acompanhar" é repetir só olhando: começa de hora em hora, na autonomia que nunca age.
    if (modo === 'acompanhar') {
      setAutonomia('observar');
      setQuando((q) => ({ ...q, modo, frequencia: 'HOURLY', intervalo: Math.max(q.intervalo, 1) }));
    } else {
      setQuando((q) => ({ ...q, modo, ...(modo === 'repetir' && q.modo === 'acompanhar' ? { frequencia: 'DAILY' as const } : {}) }));
    }
  };

  const semPrevia = impede ?? (!gatilho ? 'Preencha o horário de início.' : null);
  return (
    <Card aria-label="Novo pedido">
      <CardHeader level={3} title="Repetir ou acompanhar este comando"
                  subtitle="Um pedido é um objetivo que dura: gera uma ocorrência por data. A prévia não gasta nada; só “Confirmar e criar” cria."
                  actions={<Button size="sm" variant="ghost" iconOnly icon={X} label="Fechar o pedido" onClick={onFechar} />} />
      <CardBody className={styles.formulario}>
        {quemFaz ? (
          <p className={styles.quemFaz}>
            <Users size={14} aria-hidden /> <span>Quem faz:</span> <strong>{quemFaz}</strong>
          </p>
        ) : null}

        <Field label="Título" unit="opcional">
          {({ id }) => <TextInput id={id} value={titulo} maxLength={120} placeholder="Sem título: usa o começo do objetivo"
                                  onChange={(e) => setTitulo(e.target.value)} />}
        </Field>

        <Secao titulo="Quando">
          <div className={styles.seletor} role="group" aria-label="Quando">
            {MODOS.map((m) => (
              <button key={m.id} type="button" className={cx(styles.seletorItem, quando.modo === m.id && styles.seletorAtivo)}
                      aria-pressed={quando.modo === m.id} onClick={() => escolherModo(m.id)}>{m.rotulo}</button>
            ))}
          </div>
          {quando.modo !== 'agora' ? (
            <div className={styles.duas}>
              <Field label={agendado ? 'Começa em' : 'Quando'}
                     hint={fusoEfetivo === relogio ? 'Hora do seu relógio.' : `Hora local, no fuso do pedido (${fusoEfetivo}).`}>
                {({ id, describedBy }) => (
                  <TextInput id={id} aria-describedby={describedBy} type="datetime-local" value={quando.inicio}
                             onChange={(e) => { mudou(); setQuando({ ...quando, inicio: e.target.value }); }} />
                )}
              </Field>
              {agendado ? (
                <Field label="A cada">
                  {({ id, describedBy }) => (
                    <div className={styles.acoes}>
                      <TextInput id={id} aria-describedby={describedBy} type="number" min={1} value={quando.intervalo} style={{ width: 80 }}
                                 onChange={(e) => { mudou(); setQuando({ ...quando, intervalo: Number(e.target.value) }); }} />
                      <Select aria-label="Unidade da repetição" value={quando.frequencia}
                              onChange={(e) => { mudou(); setQuando({ ...quando, frequencia: e.target.value as Frequencia }); }}>
                        {FREQUENCIAS.map((f) => <option key={f.id} value={f.id}>{f.rotulo}</option>)}
                      </Select>
                    </div>
                  )}
                </Field>
              ) : null}
            </div>
          ) : null}
          {agendado && quando.frequencia === 'WEEKLY' ? (
            <div className={styles.dias} role="group" aria-label="Dias da semana">
              {DIAS_DA_SEMANA.map((d) => (
                <Button key={d.id} size="sm" variant={quando.dias.includes(d.id) ? 'primary' : 'outline'} aria-pressed={quando.dias.includes(d.id)}
                        onClick={() => { mudou(); setQuando({ ...quando, dias: quando.dias.includes(d.id) ? quando.dias.filter((x) => x !== d.id) : [...quando.dias, d.id] }); }}>
                  {d.rotulo}
                </Button>
              ))}
            </div>
          ) : null}
          {quando.modo !== 'agora' ? (
            <Field label="Prazo final" unit="opcional" hint="Depois dele o pedido se encerra sozinho. Hora do navegador.">
              {({ id, describedBy }) => <TextInput id={id} aria-describedby={describedBy} type="datetime-local" value={fim}
                                                   onChange={(e) => { mudou(); setFim(e.target.value); }} />}
            </Field>
          ) : null}
        </Secao>

        <Secao titulo="O que conta como feito" dica="Sem critério, só prazo, contagem ou orçamento encerram o pedido.">
          <Field label="Critérios de sucesso" unit="opcional, um por linha">
            {({ id }) => <TextArea id={id} rows={2} value={criterios} placeholder="Ex.: o relatório foi enviado"
                                   onChange={(e) => { mudou(); setCriterios(e.target.value); }} />}
          </Field>
        </Secao>

        <Secao titulo="Limites">
          <div className={styles.duas}>
            <Field label="Autonomia" hint={ROTULO_DA_AUTONOMIA[autonomia].dica}>
              {({ id, describedBy }) => (
                <Select id={id} aria-describedby={describedBy} value={autonomia}
                        onChange={(e) => { mudou(); setAutonomia(e.target.value as Autonomia); }}>
                  {(['observar', 'preparar', 'agir'] as const).map((a) => <option key={a} value={a}>{ROTULO_DA_AUTONOMIA[a].rotulo}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Máximo de ocorrências" unit="opcional">
              {({ id }) => <TextInput id={id} type="number" min={1} value={maximo} onChange={(e) => { mudou(); setMaximo(e.target.value); }} />}
            </Field>
            <Field label="Orçamento total (US$)" unit="opcional">
              {({ id }) => <TextInput id={id} type="number" min={0} step="0.01" value={orcamento} onChange={(e) => { mudou(); setOrcamento(e.target.value); }} />}
            </Field>
            <Field label="Orçamento por ocorrência (US$)" unit="opcional">
              {({ id }) => <TextInput id={id} type="number" min={0} step="0.01" value={orcamentoOcorrencia}
                                      onChange={(e) => { mudou(); setOrcamentoOcorrencia(e.target.value); }} />}
            </Field>
          </div>
        </Secao>

        <Disclosure summary="Avançado" meta="fuso, sobreposição, tentativas">
          <div className={styles.duas}>
            <Field label="Fuso" hint="O fuso em que “todo dia às 19:00” é lido.">
              {({ id, describedBy }) => <TextInput id={id} aria-describedby={describedBy} value={fuso}
                                                   onChange={(e) => { mudou(); setFuso(e.target.value); }} />}
            </Field>
            <Field label="Se a anterior ainda roda" hint="O que fazer quando uma data chega com a ocorrência anterior em andamento.">
              {({ id, describedBy }) => (
                <Select id={id} aria-describedby={describedBy} value={sobreposicao}
                        onChange={(e) => { mudou(); setSobreposicao(e.target.value as '' | Sobreposicao); }}>
                  <option value="">Padrão da autonomia</option>
                  {(Object.keys(ROTULO_DA_SOBREPOSICAO) as Sobreposicao[]).map((s) => <option key={s} value={s}>{ROTULO_DA_SOBREPOSICAO[s]}</option>)}
                </Select>
              )}
            </Field>
            <Field label="Tentativas por ocorrência" unit="opcional">
              {({ id }) => <TextInput id={id} type="number" min={1} value={tentativas} onChange={(e) => { mudou(); setTentativas(e.target.value); }} />}
            </Field>
          </div>
        </Disclosure>

        {erro ? <Banner tone="danger" icon={TriangleAlert} compact role="alert">{erro}</Banner> : null}
        {previa ? <PreviaDoPedido previa={previa.dados} resumoQuando={falaDoQuando(quando)} fuso={previa.corpo.fuso ?? fusoEfetivo} /> : null}

        <div className={styles.acoes}>
          <Button icon={CalendarClock} loading={carregando === 'previa'} disabled={carregando === 'criar'}
                  disabledReason={semPrevia} onClick={() => void verPrevia()}>
            {previa ? 'Ver a prévia de novo' : 'Ver a prévia'}
          </Button>
          <Button variant="primary" loading={carregando === 'criar'}
                  disabledReason={!previa ? 'Veja a prévia primeiro: ela é obrigatória.'
                    : !previa.dados.valido || !previa.dados.confirmacao ? 'A prévia tem bloqueios: ajuste o que ela aponta.' : null}
                  onClick={() => void criar()}>
            Confirmar e criar
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
