import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { AiStatus } from '../../api/types';

/**
 * `GET /api/ai` para os avisos de custo (geração de persona pelo papel `social`, gerador de imagem). Falhar aqui não
 * impede nada: quem usa diz que não deu para ler o provedor, em vez de fingir que é grátis.
 */
export function useAiStatus(): { ai: AiStatus | null; falhou: boolean } {
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [falhou, setFalhou] = useState(false);
  useEffect(() => {
    let vivo = true;
    api.ai()
      .then((r) => { if (vivo) setAi(r); })
      .catch(() => { if (vivo) setFalhou(true); });
    return () => { vivo = false; };
  }, []);
  return { ai, falhou };
}
