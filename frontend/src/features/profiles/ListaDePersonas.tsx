import { AtSign, Ban, MoreHorizontal, RotateCcw, Server, ShieldCheck, Smartphone, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import type { PersonaDTO } from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Checkbox } from '../../components/Field';
import { Popover } from '../../components/Popover';
import { Tooltip } from '../../components/Tooltip';
import { toast, toastError } from '../../store/toasts';
import { abaDoPedido, type Aba } from './abas';
import { estadoComposto, type EstadoComposto } from './filtroPersonas';
import { handleDe, idsDosAparelhos, nomeDe, resumoDe } from './pessoa';
import styles from './Profiles.module.css';

/** Remover, marcar bloqueada e reativar: o mesmo comportamento no cartão e na linha da tabela. */
function useAcoesPersona(pessoa: PersonaDTO, onChanged: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const nome = nomeDe(pessoa);

  async function remover() {
    // `confirm` devolve um OBJETO, que é sempre verdadeiro: testar o objeto faria "Voltar" apagar a persona e a
    // credencial do mesmo jeito. Quem decide é `confirmed`.
    const { confirmed } = await confirm({
      title: `Remover ${nome}?`,
      body: 'É apagar a pessoa: as contas e as senhas guardadas no cofre vão junto, com memória, fotos e histórico. '
        + 'Persona vinculada a aparelho ou com execução em andamento não sai.',
      confirmLabel: 'Remover',
      danger: true,
    });
    if (!confirmed) return;
    setBusy(true);
    try {
      await api.deletePersona(pessoa.id);
      toast({ tone: 'success', title: `${nome} removida` });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível remover a persona', e);
    } finally {
      setBusy(false);
    }
  }

  async function mudarStatus(status: 'active' | 'blocked') {
    setBusy(true);
    try {
      await api.patchProfile(pessoa.id, { status });
      toast({
        tone: 'success',
        title: status === 'blocked' ? `${nome} marcada como bloqueada` : `${nome} reativada`,
        message: status === 'blocked' ? 'Nenhuma tarefa será despachada para esta persona.' : 'A persona volta a receber tarefas.',
      });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível mudar a situação da persona', e);
    } finally {
      setBusy(false);
    }
  }

  return { busy, nome, remover, mudarStatus };
}

/**
 * O menu "⋯" da persona: marcar bloqueada ou reativar, e remover. Saíram de perto do "Abrir" porque tinham o
 * mesmo peso visual da ação principal, e a lixeira vermelha ficava a um clique errado de distância. Remover continua
 * pedindo confirmação, e o vermelho mora só aqui dentro.
 */
function MenuAcoesPersona({ pessoa, onChanged }: { pessoa: PersonaDTO; onChanged: () => Promise<void> }) {
  const { busy, nome, remover, mudarStatus } = useAcoesPersona(pessoa, onChanged);
  const ativa = pessoa.status === 'active';
  return (
    <Popover label={`Mais ações de ${nome}`} align="end" triggerClassName={styles.menuGatilho}
             trigger={<MoreHorizontal size={16} aria-hidden />}>
      {(fechar) => (
        <ul className={styles.menuAcoes}>
          <li>
            {/* Conta bloqueada pela plataforma: registrar aqui é o que tira a persona do despacho. Reativar é
                decisão de pessoa, depois de a conta voltar de verdade. */}
            <button type="button" className={styles.menuItem} disabled={busy}
                    onClick={() => { fechar(); void mudarStatus(ativa ? 'blocked' : 'active'); }}>
              {ativa ? <Ban size={14} aria-hidden /> : <RotateCcw size={14} aria-hidden />}
              {ativa ? 'Marcar bloqueada' : 'Reativar'}
            </button>
          </li>
          <li>
            <button type="button" className={`${styles.menuItem} ${styles.menuItemPerigo}`} disabled={busy}
                    onClick={() => { fechar(); void remover(); }}>
              <Trash2 size={14} aria-hidden /> Remover persona
            </button>
          </li>
        </ul>
      )}
    </Popover>
  );
}

/**
 * Texto numa linha só, cortado com reticências, com o conteúdo inteiro no tooltip nativo (`title`). O corte é só
 * visual: o leitor de tela lê o texto inteiro. Sem `tabIndex` de propósito: cada campo do cartão virar parada do
 * Tab faria a lista de 14 pessoas custar uma centena de teclas.
 */
function Truncado({ texto, className }: { texto: string; className?: string }) {
  return <span className={`${styles.truncado}${className ? ` ${className}` : ''}`} title={texto}>{texto}</span>;
}

/** Um selo só para a situação: "Ativa · app não instalado", com o porquê no tooltip. */
function SeloDeEstado({ estado }: { estado: EstadoComposto }) {
  return (
    <Tooltip content={`${estado.rotulo}: ${estado.explicacao}`}>
      <span tabIndex={0} className={styles.seloEstado} aria-label={`${estado.rotulo}. ${estado.explicacao}`}>
        <Badge tone={estado.tom}>{estado.rotulo}</Badge>
      </span>
    </Tooltip>
  );
}

function textoAparelhos(pessoa: PersonaDTO): string {
  const aparelhos = idsDosAparelhos(pessoa);
  if (aparelhos.length === 0) return 'não vinculado';
  return aparelhos.map((id) => (aparelhos.length > 1 && id === pessoa.instance_id ? `${id} (principal)` : id)).join(' · ');
}

/**
 * O cartão é a PESSOA: nome, @ (se houver), idade/cidade/profissão, quantas contas, o aparelho, onde os dados vivem,
 * a situação e o grupo. Layout fixo (tarefa UX 05): as mesmas linhas em todo cartão, uma linha de texto cada, com o
 * conteúdo inteiro no tooltip, então todos têm a mesma altura e nada se sobrepõe. Senha, sessão e Conectar moram na
 * guia Contas e acesso.
 */
export function PersonaCard({ pessoa, onChanged, onOpen, selecionada, onSelecionar }: {
  pessoa: PersonaDTO;
  onChanged: () => Promise<void>;
  onOpen: (aba?: Aba) => void;
  selecionada: boolean;
  onSelecionar: () => void;
}) {
  const nome = nomeDe(pessoa);
  const handle = handleDe(pessoa);
  const resumo = resumoDe(pessoa).join(' · ');
  const loc = pessoa.locality;
  const aparelhos = idsDosAparelhos(pessoa);
  const estado = estadoComposto(pessoa);
  // A linha de aviso: onde os dados vivem quando isso mudou (E9), senão o porquê de um estado que pede alguém.
  const aviso = loc?.detail && (loc.moved || !loc.available) ? loc.detail
    : estado.tom === 'warning' || estado.tom === 'danger' ? estado.explicacao : '';
  const servidor = loc ? (loc.worker_name ?? loc.worker_id ?? 'este servidor') : null;

  return (
    <Card className={`${styles.cartaoPessoa}${selecionada ? ` ${styles.cartaoSelecionado}` : ''}`}>
      <div className={styles.cartaoTopo}>
        <Avatar src={profileAvatarUrl(pessoa.id)} name={nome} size={40} />
        <div className={styles.cartaoIdentidade}>
          <h2 className={styles.cartaoNome}><Truncado texto={nome} /></h2>
          <Truncado texto={handle ? `@${handle}` : 'sem conta de cadastro'} className={styles.cartaoHandle} />
        </div>
        {/* Pelo NOME, não pelo @: pessoa sem conta também entra no lote, e o leitor de tela distingue os cartões. */}
        <Checkbox aria-label={`Selecionar ${nome}`} checked={selecionada} onChange={onSelecionar} />
      </div>
      <CardBody className={styles.cartaoCorpo}>
        <p className={styles.cardResumo}>{resumo ? <Truncado texto={resumo} /> : <span className={styles.muted}>sem biografia resumida</span>}</p>
        <dl className={`${styles.rows} ${styles.rowsCartao}`}>
          <div className={styles.row}>
            <dt><AtSign size={14} aria-hidden /> Contas</dt>
            <dd>{pessoa.accounts_count ?? 0}</dd>
          </div>
          <div className={styles.row}>
            <dt><Smartphone size={14} aria-hidden /> {aparelhos.length > 1 ? 'Aparelhos' : 'Aparelho'}</dt>
            <dd>{aparelhos.length === 0 ? <span className={styles.muted}>não vinculado</span> : <Truncado texto={textoAparelhos(pessoa)} />}</dd>
          </div>
          {/* Onde os DADOS vivem (E9). Sem aparelho não há localidade a afirmar: a linha fica, com um traço, para
              o cartão ter a mesma altura dos outros. */}
          <div className={styles.row}>
            <dt><Server size={14} aria-hidden /> Servidor</dt>
            <dd className={styles.linhaUnica}>
              {/* "localidade não registrada" era um selo que se sobrepunha ao nome do servidor; agora vai no tooltip. */}
              {servidor ? <Truncado texto={loc && !loc.known ? `${servidor} (localidade não registrada)` : servidor} />
                : <span className={styles.muted}>—</span>}
              {loc?.moved ? <Badge tone="warning">mudou de servidor</Badge> : null}
              {loc && !loc.available ? <Badge tone="warning">indisponível</Badge> : null}
            </dd>
          </div>
          <div className={styles.row}>
            <dt>Situação</dt>
            <dd className={styles.linhaUnica}><SeloDeEstado estado={estado} /></dd>
          </div>
          <div className={styles.row}>
            <dt><ShieldCheck size={14} aria-hidden /> Grupo de acesso</dt>
            <dd className={styles.linhaUnica}>{pessoa.policy_group_name
              ? <Badge tone="info">{pessoa.policy_group_name}</Badge>
              : <Truncado texto="nenhum — padrão do catálogo" className={styles.muted} />}</dd>
          </div>
        </dl>
        <p className={styles.cartaoAviso}>{aviso ? <Truncado texto={aviso} /> : null}</p>
        <div className={styles.actions}>
          {/* Nome no rótulo: a lista tem um "Abrir" por pessoa, e o leitor de tela precisa distinguir. */}
          <Button size="sm" variant="primary" onClick={() => onOpen()} aria-label={`Abrir ${nome}`}>Abrir</Button>
          {/* A ação do estado só LEVA à guia onde se resolve: daqui não se instala, conecta nem vincula nada. */}
          {estado.acao ? (
            <Button size="sm" variant="outline" onClick={() => onOpen(abaDoPedido(estado.acao!.guia))}
                    aria-label={`${estado.acao.rotulo}: abrir ${nome} na guia certa`}>
              {estado.acao.rotulo}
            </Button>
          ) : null}
          <span className={styles.actionsFim}><MenuAcoesPersona pessoa={pessoa} onChanged={onChanged} /></span>
        </div>
      </CardBody>
    </Card>
  );
}

/**
 * A mesma lista em tabela (tarefa UX 05): uma linha por pessoa, para comparar muitas de uma vez. A seleção é a
 * mesma dos cartões (vive na página), então alternar a visão não a perde.
 */
export function TabelaPersonas({ pessoas, selecionadas, onSelecionar, onOpen, onChanged }: {
  pessoas: readonly PersonaDTO[];
  selecionadas: ReadonlySet<string>;
  onSelecionar: (id: string) => void;
  onOpen: (id: string, aba?: Aba) => void;
  onChanged: () => Promise<void>;
}) {
  return (
    <div className={styles.tabelaHost}>
      <table className={styles.tabela}>
        <caption className="sr-only">Personas</caption>
        <thead>
          <tr>
            <th scope="col"><span className="sr-only">Seleção</span></th>
            <th scope="col">Persona</th>
            <th scope="col">Conta (@)</th>
            <th scope="col" className={styles.num}>Contas</th>
            <th scope="col">Aparelho</th>
            <th scope="col">Situação</th>
            <th scope="col">Grupo</th>
            <th scope="col"><span className="sr-only">Ações</span></th>
          </tr>
        </thead>
        <tbody>
          {pessoas.map((p) => {
            const nome = nomeDe(p);
            const handle = handleDe(p);
            const estado = estadoComposto(p);
            return (
              <tr key={p.id} className={selecionadas.has(p.id) ? styles.linhaSelecionada : undefined}>
                <td><Checkbox aria-label={`Selecionar ${nome}`} checked={selecionadas.has(p.id)} onChange={() => onSelecionar(p.id)} /></td>
                <td>
                  <span className={styles.tabelaPessoa}>
                    <Avatar src={profileAvatarUrl(p.id)} name={nome} size={28} />
                    <Truncado texto={nome} />
                  </span>
                </td>
                <td>{handle ? <Truncado texto={`@${handle}`} /> : <span className={styles.muted}>sem conta</span>}</td>
                <td className={styles.num}>{p.accounts_count ?? 0}</td>
                <td>{idsDosAparelhos(p).length ? <Truncado texto={textoAparelhos(p)} /> : <span className={styles.muted}>—</span>}</td>
                <td><SeloDeEstado estado={estado} /></td>
                <td>{p.policy_group_name ? <Truncado texto={p.policy_group_name} /> : <span className={styles.muted}>—</span>}</td>
                <td>
                  <span className={styles.tabelaAcoes}>
                    <Button size="sm" variant="outline" onClick={() => onOpen(p.id)} aria-label={`Abrir ${nome}`}>Abrir</Button>
                    <MenuAcoesPersona pessoa={p} onChanged={onChanged} />
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
