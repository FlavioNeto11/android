import { CalendarClock, TriangleAlert, X } from 'lucide-react';
import { useRef, useState } from 'react';
import { toApiError } from '../../api/client';
import type { Autonomia, PedidoCorpo, PedidoPrevia } from '../../api/pedidos';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { uuid } from '../../lib/ids';
import { useUiStore } from '../../store/ui';
import { toast } from '../../store/toasts';
import { apiPedidos } from './api';
import {
  DIAS_DA_SEMANA, falaDoQuando, gatilhoDoQuando, quandoInicial, type EstadoDoQuando, type Frequencia, type ModoQuando,
} from './gatilho';
import { ROTULO_DA_AUTONOMIA, instanteCanonico, mensagemDoErro } from './modelo';
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
  onFechar: () => void;
}

/**
 * Criar um pedido a partir do Comando: "Quando" + autonomia + limites → PRÉVIA (sem efeito, sem custo, sem IA) → só
 * "Confirmar e criar", com o selo da prévia, cria o pedido ativo (ADR-044). Mexeu em qualquer campo: a prévia vira
 * velha e some, e a confirmação exige uma nova.
 */
export function NovoPedido({ comando, resolverAlvos, onFechar }: Props) {
  const navegar = useUiStore((s) => s.navegar);
  const [quando, setQuando] = useState<EstadoDoQuando>(() => quandoInicial());
  const [autonomia, setAutonomia] = useState<Autonomia>('observar');
  const [titulo, setTitulo] = useState('');
  const [criterios, setCriterios] = useState('');
  const [fuso, setFuso] = useState('America/Sao_Paulo');
  const [fim, setFim] = useState('');
  const [maximo, setMaximo] = useState('');
  const [orcamento, setOrcamento] = useState('');
  const [previa, setPrevia] = useState<{ dados: PedidoPrevia; corpo: PedidoCorpo } | null>(null);
  const [carregando, setCarregando] = useState<'previa' | 'criar' | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const chave = useRef<{ impressao: string; valor: string } | null>(null);

  const mudou = () => { setPrevia(null); setErro(null); };
  const gatilho = gatilhoDoQuando(quando);
  const numero = (t: string): number | undefined => (t.trim() !== '' && Number.isFinite(Number(t)) ? Number(t) : undefined);

  const montarCorpo = async (): Promise<PedidoCorpo | null> => {
    if (!gatilho) return null;
    const alvos = await resolverAlvos();
    const lista = criterios.split('\n').map((l) => l.trim()).filter(Boolean);
    const max = numero(maximo);
    const orc = numero(orcamento);
    return {
      objetivo: comando, alvos, autonomia, fuso: fuso.trim() || 'America/Sao_Paulo', gatilhos: [gatilho],
      ...(lista.length > 0 ? { criterios_sucesso: lista } : {}),
      ...(fim ? { fim_em: instanteCanonico(new Date(fim)) } : {}),
      ...(max !== undefined ? { max_ocorrencias: max } : {}),
      ...(orc !== undefined ? { orcamento_total_usd: orc } : {}),
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

  return (
    <Card aria-label="Novo pedido">
      <div className={styles.formulario}>
        <div className={styles.topo}>
          <CalendarClock size={16} aria-hidden />
          <strong>Repetir ou acompanhar este comando</strong>
          <Button size="sm" variant="ghost" iconOnly icon={X} label="Fechar o pedido" onClick={onFechar} />
        </div>
        <p className={styles.nota}>
          Um pedido é um objetivo que dura: gera uma ocorrência por data, e cada ocorrência vira uma execução comum. A prévia
          não gasta nada e não chama IA; só “Confirmar e criar” cria o pedido.
        </p>

        <div className={styles.segmentado} role="group" aria-label="Quando">
          {MODOS.map((m) => (
            <Button key={m.id} size="sm" variant={quando.modo === m.id ? 'primary' : 'outline'} aria-pressed={quando.modo === m.id}
                    onClick={() => escolherModo(m.id)}>{m.rotulo}</Button>
          ))}
        </div>

        {quando.modo !== 'agora' ? (
          <div className={styles.duas}>
            <Field label={agendado ? 'Começa em' : 'Quando'} hint={`Hora local, no fuso do pedido (${fuso || 'America/Sao_Paulo'}).`}>
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

        <div className={styles.duas}>
          <Field label="Autonomia" hint={ROTULO_DA_AUTONOMIA[autonomia].dica}>
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={autonomia}
                      onChange={(e) => { mudou(); setAutonomia(e.target.value as Autonomia); }}>
                {(['observar', 'preparar', 'agir'] as const).map((a) => <option key={a} value={a}>{ROTULO_DA_AUTONOMIA[a].rotulo}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Título" unit="opcional">
            {({ id }) => <TextInput id={id} value={titulo} maxLength={120} placeholder="Sem título: usa o começo do objetivo"
                                    onChange={(e) => setTitulo(e.target.value)} />}
          </Field>
        </div>
        <Field label="Critérios de sucesso" unit="opcional, um por linha" hint="O que prova que o objetivo foi cumprido. Sem critério, só prazo, contagem ou orçamento encerram o pedido.">
          {({ id, describedBy }) => <TextArea id={id} aria-describedby={describedBy} rows={2} value={criterios}
                                              onChange={(e) => { mudou(); setCriterios(e.target.value); }} />}
        </Field>
        <div className={styles.duas}>
          <Field label="Fuso">
            {({ id }) => <TextInput id={id} value={fuso} onChange={(e) => { mudou(); setFuso(e.target.value); }} />}
          </Field>
          <Field label="Prazo final" unit="opcional, horário do navegador">
            {({ id }) => <TextInput id={id} type="datetime-local" value={fim} onChange={(e) => { mudou(); setFim(e.target.value); }} />}
          </Field>
          <Field label="Máximo de ocorrências" unit="opcional">
            {({ id }) => <TextInput id={id} type="number" min={1} value={maximo} onChange={(e) => { mudou(); setMaximo(e.target.value); }} />}
          </Field>
          <Field label="Orçamento total (US$)" unit="opcional">
            {({ id }) => <TextInput id={id} type="number" min={0} step="0.01" value={orcamento} onChange={(e) => { mudou(); setOrcamento(e.target.value); }} />}
          </Field>
        </div>

        {erro ? <Banner tone="danger" icon={TriangleAlert} compact role="alert">{erro}</Banner> : null}
        {previa ? <PreviaDoPedido previa={previa.dados} resumoQuando={falaDoQuando(quando)} /> : null}

        <div className={styles.acoes}>
          <Button icon={CalendarClock} loading={carregando === 'previa'} disabled={carregando === 'criar'}
                  disabledReason={!gatilho ? 'Preencha o horário de início.' : null} onClick={() => void verPrevia()}>
            {previa ? 'Ver a prévia de novo' : 'Ver a prévia'}
          </Button>
          <Button variant="primary" loading={carregando === 'criar'}
                  disabledReason={!previa ? 'Veja a prévia primeiro: ela é obrigatória.'
                    : !previa.dados.valido || !previa.dados.confirmacao ? 'A prévia tem bloqueios: ajuste o que ela aponta.' : null}
                  onClick={() => void criar()}>
            Confirmar e criar
          </Button>
        </div>
      </div>
    </Card>
  );
}
