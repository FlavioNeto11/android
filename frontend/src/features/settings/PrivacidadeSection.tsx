import { CheckCircle2, Search, ServerCrash, ShieldCheck, Trash2 } from 'lucide-react';
import { useCallback, useId, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { PortalContatoAchado, PortalExclusaoResultado, PortalMotivoMantido, PortalPedidoPor } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Checkbox, Field, TextInput } from '../../components/Field';
import { PageSection, TableWrap } from '../../components/Page';
import type { Tone } from '../../lib/status';
import { parseTs } from '../../lib/time';
import styles from './Privacidade.module.css';

/** O telefone como a pessoa escreveu no formulário: de 8 a 30 dígitos, sem os zeros da frente (o de discagem), o mesmo
 *  mínimo do formulário. A API compara o número inteiro (com o 55 opcional no brasileiro completo); a regra local só
 *  evita ir à rede com o que ela recusaria. */
const MIN_DIGITOS = 8;
const MAX_DIGITOS = 30;

const ESTADO: Record<PortalContatoAchado['estado'], { rotulo: string; tom: Tone }> = {
  pendente: { rotulo: 'Aguardando entrega', tom: 'warning' },
  entregue: { rotulo: 'Entregue', tom: 'success' },
  retido: { rotulo: 'Retido (excesso na hora)', tom: 'info' },
  descartado: { rotulo: 'Descartado', tom: 'neutral' },
};

const MOTIVO_MANTIDO: Record<PortalMotivoMantido, string> = {
  em_envio: 'a exclusão NÃO terminou: a mensagem estava saindo agora; exclua de novo em instantes',
  falhou: 'o canal não confirmou a exclusão da fila; nada foi apagado deste contato, tente de novo',
  canal_sem_exclusao: 'o canal ainda não sabe apagar; use o procedimento manual de docs/operacao.md',
};

const ORIGENS: { valor: PortalPedidoPor; rotulo: string }[] = [
  { valor: 'formulario', rotulo: 'Formulário do site' },
  { valor: 'telefone', rotulo: 'Telefone' },
  { valor: 'outro', rotulo: 'Outro' },
];

const dataHoraFmt = new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
});

/** dd/mm/aaaa hh:mm (hora local de quem opera). */
function dataHora(iso: string): string {
  const t = parseTs(iso);
  return t === null ? '—' : dataHoraFmt.format(t).replace(',', '');
}

function soDigitos(v: string): string {
  return v.replace(/\D/g, '');
}

/**
 * Exclusão de contato do site a pedido do titular (29.83). A tela nunca mostra nome, empresa nem mensagem (a API nem
 * os manda: só o final do telefone), e o texto livre fica de fora de propósito, porque vira lugar de dado pessoal. A
 * confirmação diz o que SERÁ apagado e o que NÃO será: apagar de verdade é só parte do que o titular pediu, e quem
 * opera precisa saber o resto (cópias de segurança, mensagens antigas no Telegram) antes de clicar.
 */
export function PrivacidadeSection() {
  const [telefone, setTelefone] = useState('');
  const [erroTelefone, setErroTelefone] = useState<string | null>(null);
  // O telefone da última busca: a busca depois da exclusão usa ele, não o que a pessoa já tenha editado no campo.
  const [telefoneBuscado, setTelefoneBuscado] = useState<string | null>(null);
  const [contatos, setContatos] = useState<PortalContatoAchado[] | null>(null);
  const [selecionados, setSelecionados] = useState<Set<number>>(new Set());
  const [pedidoPor, setPedidoPor] = useState<PortalPedidoPor | null>(null);
  const [buscando, setBuscando] = useState(false);
  const [excluindo, setExcluindo] = useState(false);
  const [resultado, setResultado] = useState<PortalExclusaoResultado | null>(null);
  const [erro, setErro] = useState<{ message: string; hint: string } | null>(null);
  const nomeOrigem = useId();

  const buscar = useCallback(async (digitos: string) => {
    setBuscando(true);
    setErro(null);
    try {
      const r = await api.portalBuscarContatos(digitos);
      setTelefoneBuscado(digitos);
      setContatos(r.contatos);
      // Só fica marcado o que ainda existe: depois de excluir, o que sobrou não herda a marca de quem já se foi.
      setSelecionados((atual) => new Set(r.contatos.filter((c) => atual.has(c.id)).map((c) => c.id)));
    } catch (e) {
      const err = toApiError(e);
      setErro({ message: err.message, hint: hintForError(err) });
    } finally {
      setBuscando(false);
    }
  }, []);

  const aoBuscar = async () => {
    const digitos = soDigitos(telefone);
    const semZeros = digitos.replace(/^0+/, '');
    if (semZeros.length < MIN_DIGITOS || semZeros.length > MAX_DIGITOS) {
      setErroTelefone(`Informe o telefone como a pessoa escreveu, com DDD se ela usou (${MIN_DIGITOS} a ${MAX_DIGITOS} dígitos).`);
      return;
    }
    setErroTelefone(null);
    setResultado(null);
    setSelecionados(new Set());
    await buscar(digitos);
  };

  const alternar = (id: number) => setSelecionados((atual) => {
    const novo = new Set(atual);
    if (novo.has(id)) novo.delete(id); else novo.add(id);
    return novo;
  });

  const todosMarcados = !!contatos && contatos.length > 0 && selecionados.size === contatos.length;
  const alternarTodos = () => setSelecionados(todosMarcados ? new Set() : new Set((contatos ?? []).map((c) => c.id)));

  const podeExcluir = selecionados.size > 0 && pedidoPor !== null;

  const aoExcluir = async () => {
    if (!podeExcluir || pedidoPor === null) return;
    const ids = (contatos ?? []).filter((c) => selecionados.has(c.id)).map((c) => c.id);
    const n = ids.length;
    const { confirmed } = await confirm({
      title: 'Apagar definitivamente?',
      confirmLabel: 'Apagar definitivamente',
      danger: true,
      body: (
        <>
          {/* As duas frases do Telegram são condição da orquestradora (04/10) para o bot apagar também as respostas do
              dono: ele decide sabendo. O texto é o mesmo da regra C-27 da Canais. */}
          <p className={styles.paragrafo}>
            <strong>Será apagado:</strong>{' '}
            {n === 1
              ? 'Excluir este contato apaga os dados dele na Central (nome, empresa, telefone e mensagem). '
                + 'Também serão apagados do seu Telegram o aviso deste contato e as suas respostas a esse aviso, quando o '
                + 'Telegram ainda permitir (até 47 horas depois do envio).'
              : `Excluir estes ${n} contatos apaga os dados deles na Central (nome, empresa, telefone e mensagem). `
                + 'Também serão apagados do seu Telegram os avisos destes contatos e as suas respostas a esses avisos, '
                + 'quando o Telegram ainda permitir (até 47 horas depois do envio).'}{' '}
            O que não der para apagar sozinho aparece numa lista com a hora, para você apagar à mão.
          </p>
          <p className={styles.paragrafo}><strong>Não será apagado:</strong></p>
          <ul className={styles.confirmaLista}>
            <li>
              As cópias de segurança: elas saem pela rotina, em cerca de duas semanas. A mais nova e as feitas à mão não
              saem sozinhas.
            </li>
            <li>
              As mensagens no Telegram (do bot e as suas respostas) com mais de 47 horas: ficam listadas para você apagar à
              mão no chat.
            </li>
          </ul>
          <p className={styles.paragrafo}><strong>Não há como desfazer.</strong></p>
        </>
      ),
    });
    if (!confirmed) return;
    setExcluindo(true);
    setErro(null);
    try {
      const r = await api.portalExcluirContatos(ids, pedidoPor);
      setResultado(r);
      if (telefoneBuscado) await buscar(telefoneBuscado);
    } catch (e) {
      const err = toApiError(e);
      setErro({ message: err.message, hint: hintForError(err) });
    } finally {
      setExcluindo(false);
    }
  };

  return (
    <PageSection
      title={<><ShieldCheck size={16} aria-hidden className={styles.inlineIcon} /> Exclusão a pedido do titular</>}
      subtitle="A pessoa que mandou mensagem pelo site pede para apagar os dados dela. Busque pelo telefone que ela informou, como ela escreveu (com DDD, se usou)."
    >
      {erro ? (
        <Banner tone="warning" icon={ServerCrash} compact title="Não foi possível concluir">
          {erro.message}{erro.hint ? ` ${erro.hint}` : ''}
        </Banner>
      ) : null}

      <form className={styles.busca} onSubmit={(e) => { e.preventDefault(); void aoBuscar(); }} noValidate>
        <Field label="Telefone (com DDD, se a pessoa usou)" error={erroTelefone} className={styles.campoTelefone}>
          {({ id, describedBy, invalid }) => (
            <TextInput
              id={id} type="tel" inputMode="tel" autoComplete="off" invalid={invalid} aria-describedby={describedBy}
              placeholder="11 90000-0001" value={telefone}
              onChange={(e) => { setTelefone(e.target.value); if (erroTelefone) setErroTelefone(null); }}
            />
          )}
        </Field>
        <Button type="submit" icon={Search} loading={buscando} disabled={excluindo}>Buscar</Button>
      </form>

      {contatos !== null ? (
        contatos.length === 0 ? (
          <p className={styles.muted}>Nenhum contato com esse telefone.</p>
        ) : (
          <>
            <div className={styles.barra}>
              <p className={styles.muted} aria-live="polite">
                {contatos.length === 1 ? '1 contato encontrado' : `${contatos.length} contatos encontrados`} ·{' '}
                {selecionados.size} {selecionados.size === 1 ? 'selecionado' : 'selecionados'}
              </p>
              <Button size="sm" variant="outline" onClick={alternarTodos} disabled={excluindo}>
                {todosMarcados ? 'Desmarcar todos' : 'Selecionar todos'}
              </Button>
            </div>
            <TableWrap label="Contatos encontrados">
              <table>
                <thead>
                  <tr>
                    <th scope="col" className={styles.colCaixa}><span className="sr-only">Selecionar</span></th>
                    <th scope="col">Contato</th>
                    <th scope="col">Recebido em</th>
                    <th scope="col">Situação</th>
                    <th scope="col">Telefone</th>
                  </tr>
                </thead>
                <tbody>
                  {contatos.map((c) => {
                    const estado = ESTADO[c.estado] ?? { rotulo: c.estado, tom: 'neutral' as const };
                    return (
                      <tr key={c.id}>
                        <td>
                          <Checkbox
                            aria-label={`Contato nº ${c.id}`} checked={selecionados.has(c.id)}
                            onChange={() => alternar(c.id)} disabled={excluindo}
                          />
                        </td>
                        <td>Contato nº {c.id}</td>
                        <td>{dataHora(c.criado_em)}</td>
                        <td><Badge tone={estado.tom} size="sm">{estado.rotulo}</Badge></td>
                        <td>telefone final {c.final}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>

            <fieldset className={styles.origem}>
              <legend className={styles.origemLegenda}>O pedido chegou por</legend>
              {ORIGENS.map((o) => (
                <label key={o.valor} className={styles.opcao}>
                  <input
                    type="radio" name={nomeOrigem} value={o.valor} checked={pedidoPor === o.valor}
                    onChange={() => setPedidoPor(o.valor)} disabled={excluindo}
                  />
                  <span>{o.rotulo}</span>
                </label>
              ))}
            </fieldset>

            <div className={styles.acoes}>
              <Button
                variant="danger" icon={Trash2} loading={excluindo} disabled={!podeExcluir}
                onClick={() => void aoExcluir()}
              >
                Excluir selecionados
              </Button>
            </div>
          </>
        )
      ) : null}

      {resultado ? <Resultado r={resultado} /> : null}
    </PageSection>
  );
}

function Resultado({ r }: { r: PortalExclusaoResultado }) {
  const incompleto = r.mantidos.length > 0 || r.mensagens_a_mao.length > 0;
  return (
    <Banner
      tone={incompleto ? 'warning' : 'success'} icon={CheckCircle2} role="status"
      title={r.apagados.length === 1 ? '1 contato apagado' : `${r.apagados.length} contatos apagados`}
    >
      <p className={styles.paragrafo}>
        {r.mensagens_apagadas === 1 ? '1 mensagem do bot apagada' : `${r.mensagens_apagadas} mensagens do bot apagadas`} no Telegram.
      </p>
      {r.mensagens_a_mao.length > 0 ? (
        <>
          <p className={styles.paragrafo}>Apague à mão no chat do Telegram (o bot não apaga mensagem com mais de 47 horas, e uma mensagem que pode ter saído
            sem registro também entra aqui):</p>
          <ul className={styles.lista}>
            {r.mensagens_a_mao.map((m, i) => (
              <li key={`${m.contato_id}-${i}`}>Contato nº {m.contato_id}, enviada (ou que pode ter saído) em {dataHora(m.enviada_em)}</li>
            ))}
          </ul>
        </>
      ) : null}
      {r.mantidos.some((m) => m.motivo === 'em_envio') ? (
        // A garantia "nada sai depois da exclusão" depende deste segundo gesto (revisão do #340): o aviso que já
        // estava saindo termina de sair, e é a nova exclusão que o apaga do chat ou o põe na lista à mão.
        <p className={styles.paragrafo}>
          <strong>A exclusão não terminou.</strong> Uma mensagem estava saindo para o Telegram agora. Exclua de novo em
          instantes: é a segunda exclusão que apaga essa mensagem do chat (ou a põe na lista para apagar à mão).
        </p>
      ) : null}
      {r.mantidos.length > 0 ? (
        <>
          <p className={styles.paragrafo}>Não apagados:</p>
          <ul className={styles.lista}>
            {r.mantidos.map((m) => (
              <li key={m.id}>Contato nº {m.id}: {MOTIVO_MANTIDO[m.motivo] ?? m.motivo}</li>
            ))}
          </ul>
        </>
      ) : null}
      {r.inexistentes.length > 0 ? (
        <p className={styles.paragrafo}>
          {r.inexistentes.map((id) => `Contato nº ${id}`).join(', ')}: já não existiam.
        </p>
      ) : null}
      {r.sem_canal ? <p className={styles.paragrafo}>Sem canal de aviso nesta instalação.</p> : null}
    </Banner>
  );
}
