/**
 * 31.88 F2 (adendo v1.71): "Mudar a quem vale" no fluxo ENSINADO do Livro. Chama `PUT /api/flows/{id}/scope`: gesto de
 * PESSOA (nunca da IA), que amplia ou restringe a quem o fluxo vale depois de salvo; vazio nos dois = todos. A API não
 * devolve o escopo de agora antes do gesto, então a tela não finge saber: diz que o que for marcado SUBSTITUI o escopo e
 * mostra o resultado devolvido pelo servidor. O fluxo em prova só casa para a persona que ensinou, qualquer que seja a
 * escolha: a escolha vale depois dela.
 */
import { RefreshCw, ServerCrash } from 'lucide-react';
import { useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { InstagramProfile, PolicyGroup } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { toast, toastError } from '../../store/toasts';
import { ATE_A_PROVA, textoDoEscopo } from '../training/ValePara';
import styles from './Aprendizado.module.css';

interface Listas { perfis: InstagramProfile[]; grupos: PolicyGroup[] }

const alterna = (s: Set<string>, id: string): Set<string> => {
  const n = new Set(s);
  if (n.has(id)) n.delete(id);
  else n.add(id);
  return n;
};

export function EscopoDoFluxo({ flowId }: { flowId: string }) {
  const [listas, setListas] = useState<Listas | null>(null);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [perfis, setPerfis] = useState<Set<string>>(new Set());
  const [grupos, setGrupos] = useState<Set<string>>(new Set());
  const [aplicando, setAplicando] = useState(false);
  const [aplicado, setAplicado] = useState<string | null>(null);

  const carregar = async () => {
    setCarregando(true);
    setErro(null);
    const [p, g] = await Promise.allSettled([api.listProfiles(), api.listPolicyGroups()]);
    const falha = [p, g].find((r): r is PromiseRejectedResult => r.status === 'rejected');
    if (falha) {
      const e = toApiError(falha.reason);
      setErro(`${e.message} ${hintForError(e)}`.trim());
      setListas(null);
    } else {
      setListas({ perfis: (p as PromiseFulfilledResult<InstagramProfile[]>).value, grupos: (g as PromiseFulfilledResult<PolicyGroup[]>).value });
    }
    setCarregando(false);
  };

  const aplicar = async () => {
    if (!listas) return;
    setAplicando(true);
    try {
      const r = await api.setFlowScope(flowId, { profile_ids: [...perfis].sort(), group_ids: [...grupos].sort() });
      const texto = textoDoEscopo({ on_proof: 'todos', profile_ids: r.profile_ids, group_ids: r.group_ids }, listas.perfis, listas.grupos);
      setAplicado(texto);
      toast({ tone: 'success', title: 'A quem o fluxo vale mudou', message: `Agora vale para ${texto}.` });
    } catch (e) {
      toastError('Não foi possível mudar a quem o fluxo vale', e);
    } finally {
      setAplicando(false);
    }
  };

  return (
    <Disclosure bare summary="Mudar a quem vale" onFirstOpen={() => void carregar()}>
      {() => (
        <div className={styles.confirmacao}>
          <p className={styles.secaoLead}>
            O que você marcar aqui substitui a quem o fluxo vale, e nada marcado = todos os perfis. {ATE_A_PROVA}
          </p>
          {erro ? (
            <Banner tone="danger" icon={ServerCrash} compact role="alert" title="A lista de perfis e grupos não carregou"
                    actions={<Button size="sm" variant="outline" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>Tentar de novo</Button>}>
              {erro}
            </Banner>
          ) : !listas ? (
            <LoadingRegion label="Carregando perfis e grupos…"><Skeleton height={20} width="60%" /></LoadingRegion>
          ) : (
            <>
              <div className={styles.escopoLista}>
                {listas.grupos.map((g) => (
                  <label key={g.id}><input type="checkbox" aria-label={`Grupo ${g.name}`} checked={grupos.has(g.id)}
                                           onChange={() => setGrupos((x) => alterna(x, g.id))} /> grupo {g.name}</label>
                ))}
                {listas.perfis.map((p) => (
                  <label key={p.id}><input type="checkbox" aria-label={`@${p.username}`} checked={perfis.has(p.id)}
                                           onChange={() => setPerfis((x) => alterna(x, p.id))} /> @{p.username}</label>
                ))}
              </div>
              <div className={styles.itemAcoes}>
                <Button size="sm" variant="primary" loading={aplicando} onClick={() => void aplicar()}>Aplicar</Button>
              </div>
              {aplicado ? <p className={styles.secaoLead} role="status">Agora vale para {aplicado}.</p> : null}
            </>
          )}
        </div>
      )}
    </Disclosure>
  );
}
