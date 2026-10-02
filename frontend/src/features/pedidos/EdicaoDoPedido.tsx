import { TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { toApiError } from '../../api/client';
import type { Autonomia, PedidoDetalhe, PedidoEdicao, PedidoEdicaoResultado } from '../../api/pedidos';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { scalarToText } from '../../lib/format';
import { toast } from '../../store/toasts';
import { apiPedidos } from './api';
import { ROTULO_DA_AUTONOMIA, formatUsd, instanteCanonico, mensagemDoErro, paraCampoLocal } from './modelo';
import { ProximasDatas } from './PreviaDoPedido';
import styles from './Pedidos.module.css';

/** O que entra no selo da prévia (adendo v0.45): mexer nisso exige a `confirmacao` que o `dry_run` devolveu. */
const FORA_DO_SELO: ReadonlySet<string> = new Set(['titulo', 'contexto', 'criterios_sucesso']);

interface Props {
  pedido: PedidoDetalhe;
  onFechar: () => void;
  /** Chamado depois de aplicar (ou quando a versão ficou velha): a tela relê o detalhe. */
  onMudou: () => void;
}

const textoDe = (n: number | null): string => (n === null ? '' : String(n));
const numeroOuNulo = (t: string): number | null => (t.trim() !== '' && Number.isFinite(Number(t)) ? Number(t) : null);

/**
 * Editar o pedido = nova versão. Primeiro uma prévia (`dry_run`: o que muda, as próximas datas antes e depois, quantas
 * ocorrências são refeitas), depois "Aplicar", que leva a `versao` que a pessoa viu e, se a edição mexe no selo, a
 * `confirmacao` da prévia. Só os campos que mudaram vão no corpo. A agenda (gatilhos) não se edita aqui.
 */
export function EdicaoDoPedido({ pedido, onFechar, onMudou }: Props) {
  const [titulo, setTitulo] = useState(pedido.titulo);
  const [contexto, setContexto] = useState(pedido.contexto ?? '');
  const [criterios, setCriterios] = useState((pedido.criterios_sucesso ?? []).join('\n'));
  const [autonomia, setAutonomia] = useState<Autonomia>(pedido.autonomia);
  const [maximo, setMaximo] = useState(textoDe(pedido.max_ocorrencias));
  const [total, setTotal] = useState(textoDe(pedido.orcamento_total_usd));
  const [porOcorrencia, setPorOcorrencia] = useState(textoDe(pedido.orcamento_ocorrencia_usd));
  const [fim, setFim] = useState(paraCampoLocal(pedido.fim_em));
  const [previa, setPrevia] = useState<PedidoEdicaoResultado | null>(null);
  const [carregando, setCarregando] = useState<'previa' | 'aplicar' | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  const mudou = () => { setPrevia(null); setErro(null); };

  /** Só o que difere do pedido que a pessoa abriu. */
  const campos = (): Omit<PedidoEdicao, 'versao'> => {
    const c: Omit<PedidoEdicao, 'versao'> = {};
    if (titulo.trim() && titulo.trim() !== pedido.titulo) c.titulo = titulo.trim();
    if (contexto.trim() !== (pedido.contexto ?? '')) c.contexto = contexto.trim();
    const lista = criterios.split('\n').map((l) => l.trim()).filter(Boolean);
    if (JSON.stringify(lista) !== JSON.stringify(pedido.criterios_sucesso ?? [])) c.criterios_sucesso = lista;
    if (autonomia !== pedido.autonomia) c.autonomia = autonomia;
    const max = numeroOuNulo(maximo);
    if (max !== null && max !== pedido.max_ocorrencias) c.max_ocorrencias = max;
    const tot = numeroOuNulo(total);
    if (tot !== null && tot !== pedido.orcamento_total_usd) c.orcamento_total_usd = tot;
    const por = numeroOuNulo(porOcorrencia);
    if (por !== null && por !== pedido.orcamento_ocorrencia_usd) c.orcamento_ocorrencia_usd = por;
    if (fim && fim !== paraCampoLocal(pedido.fim_em)) c.fim_em = instanteCanonico(new Date(fim));
    return c;
  };
  const alterados = Object.keys(campos());

  const rodar = async (dryRun: boolean) => {
    setCarregando(dryRun ? 'previa' : 'aplicar');
    setErro(null);
    try {
      const c = campos();
      const precisaSelo = Object.keys(c).some((k) => !FORA_DO_SELO.has(k));
      const r = await apiPedidos.editar(pedido.id, {
        ...c, versao: pedido.versao, ...(dryRun ? { dry_run: true } : {}),
        ...(!dryRun && precisaSelo && previa?.confirmacao ? { confirmacao: previa.confirmacao } : {}),
      });
      if (dryRun) { setPrevia(r); return; }
      toast({ tone: 'success', title: 'Pedido editado', message: `Nova versão: ${r.pedido.versao}.` });
      onMudou();
      onFechar();
    } catch (e) {
      const err = toApiError(e);
      setErro(mensagemDoErro(err));
      if (err.code === 'versao_desatualizada') { onMudou(); setPrevia(null); }
      if (err.code === 'previa_nao_confirmada') setPrevia(null);
    } finally {
      setCarregando(null);
    }
  };

  return (
    <Dialog open onClose={onFechar} title={`Editar “${pedido.titulo}”`} size="lg"
            footer={(
              <>
                <Button variant="ghost" onClick={onFechar}>Voltar</Button>
                <Button loading={carregando === 'previa'} disabledReason={alterados.length === 0 ? 'Nada mudou ainda.' : null}
                        onClick={() => void rodar(true)}>
                  Ver o que muda
                </Button>
                <Button variant="primary" loading={carregando === 'aplicar'}
                        disabledReason={!previa ? 'Veja primeiro o que muda.' : null} onClick={() => void rodar(false)}>
                  Aplicar edição
                </Button>
              </>
            )}>
      <div className={styles.formulario}>
        <p className={styles.nota}>Versão atual: {pedido.versao}. Aplicar cria a versão {pedido.versao + 1}; as ocorrências que ainda não foram despachadas são refeitas, e nenhuma execução em curso é cancelada.</p>
        <Field label="Título">{({ id }) => <TextInput id={id} value={titulo} maxLength={120} onChange={(e) => { mudou(); setTitulo(e.target.value); }} />}</Field>
        <Field label="Contexto" unit="opcional">{({ id }) => <TextArea id={id} rows={2} value={contexto} onChange={(e) => { mudou(); setContexto(e.target.value); }} />}</Field>
        <Field label="Critérios de sucesso" unit="um por linha">
          {({ id }) => <TextArea id={id} rows={3} value={criterios} onChange={(e) => { mudou(); setCriterios(e.target.value); }} />}
        </Field>
        <div className={styles.duas}>
          <Field label="Autonomia" hint={ROTULO_DA_AUTONOMIA[autonomia].dica}>
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={autonomia} onChange={(e) => { mudou(); setAutonomia(e.target.value as Autonomia); }}>
                {(['observar', 'preparar', 'agir'] as const).map((a) => <option key={a} value={a}>{ROTULO_DA_AUTONOMIA[a].rotulo}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Máximo de ocorrências">{({ id }) => <TextInput id={id} type="number" min={1} value={maximo} onChange={(e) => { mudou(); setMaximo(e.target.value); }} />}</Field>
          <Field label="Orçamento total (US$)">{({ id }) => <TextInput id={id} type="number" min={0} step="0.01" value={total} onChange={(e) => { mudou(); setTotal(e.target.value); }} />}</Field>
          <Field label="Orçamento por ocorrência (US$)">{({ id }) => <TextInput id={id} type="number" min={0} step="0.01" value={porOcorrencia} onChange={(e) => { mudou(); setPorOcorrencia(e.target.value); }} />}</Field>
          <Field label="Prazo final" unit="horário do navegador">{({ id }) => <TextInput id={id} type="datetime-local" value={fim} onChange={(e) => { mudou(); setFim(e.target.value); }} />}</Field>
        </div>

        {erro ? <Banner tone="danger" icon={TriangleAlert} compact role="alert">{erro}</Banner> : null}
        {previa ? (
          <section className={styles.previa} aria-label="O que muda">
            <h3>O que muda</h3>
            {previa.mudancas.length === 0 ? <p className={styles.nota}>Nenhuma mudança de conteúdo.</p> : (
              <ul aria-label="Mudanças">
                {previa.mudancas.map((m) => (
                  <li key={m.campo}><strong>{m.campo}</strong>: {scalarToText(m.de)} → {scalarToText(m.para)}</li>
                ))}
              </ul>
            )}
            <span className={styles.nota}>{previa.ocorrencias_refeitas} {previa.ocorrencias_refeitas === 1 ? 'ocorrência é refeita' : 'ocorrências são refeitas'} na versão nova.</span>
            <div className={styles.duas}>
              <ProximasDatas datas={previa.proximas_antes} titulo="Próximas datas antes" />
              <ProximasDatas datas={previa.proximas_depois} titulo="Próximas datas depois" />
            </div>
            <span className={styles.nota}>
              Custo estimado: {previa.custo.base === 'sem_base' || previa.custo.por_mes_usd === null
                ? 'sem base de custo' : `${formatUsd(previa.custo.por_mes_usd)} por mês`}
            </span>
          </section>
        ) : null}
      </div>
    </Dialog>
  );
}
