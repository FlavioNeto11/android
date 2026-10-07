/**
 * 31.168 (adendo v1.97, K-106): religar para uso real o fluxo que nasceu de uma prova. Todo fluxo de prova termina desligado
 * e a volta é um gesto de PESSOA, com o porquê (a trilha grava "religado para uso real: <motivo>") e, se quiser, a quem
 * ele passa a valer: a prova o deixou só para quem ensinou. `PUT /api/flows/{id}` com `status: active`, `motivo` e
 * `escopo` opcional. O escopo só vai no corpo quando a pessoa pede para trocá-lo; "manter" não manda nada.
 */
import { RefreshCw, ServerCrash } from 'lucide-react';
import { useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { InstagramProfile, PolicyGroup } from '../../api/types';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Checkbox, Field, TextArea } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { toLoadError } from '../../lib/loadError';
import { toast } from '../../store/toasts';
import { MOTIVO_MAX, erroDoMotivo } from './model';
import styles from './Aprendizado.module.css';

interface Listas { perfis: InstagramProfile[]; grupos: PolicyGroup[] }

const alterna = (s: Set<string>, id: string): Set<string> => {
  const n = new Set(s);
  if (n.has(id)) n.delete(id);
  else n.add(id);
  return n;
};

/** A pessoa pelo nome da persona; nunca o @ da conta nem o e-mail. */
const rotuloDoPerfil = (p: InstagramProfile): string => p.persona_name?.trim() || p.display_name?.trim() || 'Persona sem nome';

export function ReligarFluxoDeProva({ flowId, onMudou }: { flowId: string; onMudou?: () => void }) {
  const [aberto, setAberto] = useState(false);
  const [motivo, setMotivo] = useState('');
  const [trocar, setTrocar] = useState(false);
  const [listas, setListas] = useState<Listas | null>(null);
  const [carregando, setCarregando] = useState(false);
  const [erroDaLista, setErroDaLista] = useState<string | null>(null);
  const [perfis, setPerfis] = useState<Set<string>>(new Set());
  const [grupos, setGrupos] = useState<Set<string>>(new Set());
  const [enviando, setEnviando] = useState(false);
  const [recusa, setRecusa] = useState<string | null>(null);

  const carregar = async () => {
    setCarregando(true);
    setErroDaLista(null);
    const [p, g] = await Promise.allSettled([api.listProfiles(), api.listPolicyGroups()]);
    const falha = [p, g].find((r): r is PromiseRejectedResult => r.status === 'rejected');
    if (falha) {
      const e = toApiError(falha.reason);
      setErroDaLista(`${e.message} ${hintForError(e)}`.trim());
      setListas(null);
    } else {
      setListas({ perfis: (p as PromiseFulfilledResult<InstagramProfile[]>).value, grupos: (g as PromiseFulfilledResult<PolicyGroup[]>).value });
    }
    setCarregando(false);
  };

  const alternarTroca = (sim: boolean) => {
    setTrocar(sim);
    if (sim && !listas && !carregando) void carregar();
  };

  const invalido = erroDoMotivo(motivo)
    ?? (trocar && !listas ? 'Espere a lista de perfis e grupos carregar, ou desmarque a troca de escopo.' : null);

  const fechar = () => { setAberto(false); setMotivo(''); setTrocar(false); setPerfis(new Set()); setGrupos(new Set()); setRecusa(null); };

  const religar = async () => {
    if (invalido || enviando) return;
    setEnviando(true);
    setRecusa(null);
    try {
      await api.updateFlow(flowId, {
        status: 'active', motivo: motivo.trim(),
        ...(trocar ? { escopo: { profile_ids: [...perfis].sort(), group_ids: [...grupos].sort() } } : {}),
      });
      toast({ tone: 'success', title: 'Fluxo religado para uso real', message: 'A trilha do item guarda o motivo; a data aparece no selo "Em uso real".' });
      fechar();
      onMudou?.();
    } catch (e) {
      const r = toLoadError(e);
      setRecusa(`${r.message} ${r.hint}`.trim());
    } finally {
      setEnviando(false);
    }
  };

  if (!aberto) {
    return (
      <div className={styles.itemAcoes}>
        <Button size="sm" variant="primary" aria-expanded={false} onClick={() => setAberto(true)}>Religar para uso real</Button>
      </div>
    );
  }

  const semMarca = perfis.size === 0 && grupos.size === 0;
  return (
    <form className={styles.confirmacao} onSubmit={(e) => { e.preventDefault(); void religar(); }}>
      <p className={styles.secaoLead}>
        Este fluxo nasceu de uma prova e está desligado. Religar o coloca para valer em uso real. O porquê fica na trilha do item e a marca
        &ldquo;Nascido de uma prova&rdquo; continua.
      </p>
      <Field label="Por que ele volta ao uso real" hint="Obrigatório: vai para a trilha como “religado para uso real: …”." error={recusa}>
        {({ id, describedBy, invalid }) => (
          <TextArea id={id} aria-describedby={describedBy} invalid={invalid} rows={2} maxLength={MOTIVO_MAX} value={motivo}
                    placeholder="Ex.: provado na operação de 07/10; vale para as personas do lote" onChange={(e) => setMotivo(e.target.value)} />
        )}
      </Field>
      <Checkbox label="Trocar a quem o fluxo vale" checked={trocar} onChange={(e) => alternarTroca(e.target.checked)} />
      <p className={styles.secaoLead}>
        {trocar
          ? 'O que você marcar substitui a quem o fluxo vale. Nada marcado = todos os perfis.'
          : 'Sem trocar, ele continua valendo para quem a prova deixou (a persona que ensinou).'}
      </p>
      {trocar ? (
        erroDaLista ? (
          <Banner tone="danger" icon={ServerCrash} compact role="alert" title="A lista de perfis e grupos não carregou"
                  actions={<Button size="sm" variant="outline" icon={RefreshCw} loading={carregando} onClick={() => void carregar()}>Tentar de novo</Button>}>
            {erroDaLista}
          </Banner>
        ) : !listas ? (
          <LoadingRegion label="Carregando perfis e grupos…"><Skeleton height={20} width="60%" /></LoadingRegion>
        ) : (
          <div className={styles.escopoLista}>
            {listas.grupos.map((g) => (
              <label key={g.id}><input type="checkbox" aria-label={`Grupo ${g.name}`} checked={grupos.has(g.id)}
                                       onChange={() => setGrupos((x) => alterna(x, g.id))} /> grupo {g.name}</label>
            ))}
            {listas.perfis.map((p) => (
              <label key={p.id}><input type="checkbox" aria-label={`Persona ${rotuloDoPerfil(p)}`} checked={perfis.has(p.id)}
                                       onChange={() => setPerfis((x) => alterna(x, p.id))} /> {rotuloDoPerfil(p)}</label>
            ))}
          </div>
        )
      ) : null}
      {trocar && listas && semMarca ? <p className={styles.secaoLead} role="status">Nada marcado: o fluxo passa a valer para todos os perfis.</p> : null}
      <div className={styles.itemAcoes}>
        <Button type="submit" size="sm" variant="primary" loading={enviando} disabledReason={invalido}>Religar para uso real</Button>
        <Button size="sm" variant="ghost" disabled={enviando} onClick={fechar}>Cancelar</Button>
      </div>
    </form>
  );
}
