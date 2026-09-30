import { CheckCircle2 } from 'lucide-react';
import { useState } from 'react';
import { api } from '../../api/client';
import type { Approval } from '../../api/types';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { Field, TextArea } from '../../components/Field';
import { hashDe } from '../../lib/rotas';
import { toast, toastError } from '../../store/toasts';
import { usePendenciasStore } from '../pendencias/store';
import { Carregando, useLista } from './detalheComum';
import type { Pessoa } from './pessoa';
import styles from './Profiles.module.css';

// ---------------------------------------------------------------- aprovações
export function AbaAprovacoes({ profile }: { profile: Pessoa }) {
  const [itens, recarregar] = useLista<Approval>(() => api.listApprovals('pending', profile.id), [profile.id]);
  const [editando, setEditando] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  async function decidir(a: Approval, verbo: 'approve' | 'edit' | 'reject') {
    setBusy(a.id);
    try {
      await api.decideApproval(a.id, verbo, verbo === 'edit' ? { content: editando[a.id] ?? '' } : {});
      await recarregar();
      void usePendenciasStore.getState().atualizar();
      toast({
        tone: verbo === 'reject' ? 'info' : 'success',
        title: verbo === 'approve' ? 'Aprovado' : verbo === 'edit' ? 'Editado e aprovado' : 'Rejeitado',
        message: verbo === 'reject' ? 'Nada será enviado neste alvo.' : 'O item volta para a fila.',
      });
    } catch (e) {
      toastError('Não foi possível decidir', e);
    } finally {
      setBusy(null);
    }
  }

  if (itens === null) return <Carregando />;
  if (itens.length === 0) {
    return (
      <EmptyState icon={CheckCircle2} title="Nada para aprovar"
                  hint={<>Ações com aprovação exigida aparecem aqui antes de acontecer. <a href={hashDe('pendencias')}>Ver todas as pendências</a>.</>}>
        Nenhuma pendência.
      </EmptyState>
    );
  }
  return (
    <>
    <p className={styles.lead}>Só as desta persona. <a href={hashDe('pendencias')}>Ver todas as pendências</a>.</p>
    <div className={styles.grid}>
      {itens.map((a) => (
        <Card key={a.id}>
          <CardHeader title={a.summary} subtitle={`${a.capability} · ${a.target ?? 'sem alvo'}`} />
          <CardBody className={styles.form}>
            <Field label="Conteúdo que será enviado">
              {({ id }) => (
                <TextArea id={id} rows={3} defaultValue={a.content ?? ''}
                          onChange={(e) => setEditando((s) => ({ ...s, [a.id]: e.target.value }))} />
              )}
            </Field>
            <div className={styles.actions}>
              <Button size="sm" loading={busy === a.id} onClick={() => void decidir(a, 'approve')}>Aprovar</Button>
              <Button size="sm" variant="secondary" loading={busy === a.id}
                      disabledReason={(editando[a.id] ?? '').trim()
                        ? null : 'Altere o texto para poder aprovar a edição.'}
                      onClick={() => void decidir(a, 'edit')}>Editar</Button>
              <Button size="sm" variant="ghost" loading={busy === a.id}
                      onClick={() => void decidir(a, 'reject')}>Rejeitar</Button>
            </div>
          </CardBody>
        </Card>
      ))}
    </div>
    </>
  );
}
