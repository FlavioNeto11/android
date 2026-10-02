import { PowerOff } from 'lucide-react';
import { Banner } from '../../components/Banner';

/**
 * (28.12) O laço de pedidos não roda nesta instalação (`pedidos.enabled: false`). Sem este aviso, "próxima 08:00" na lista
 * prometia o que não vai acontecer: nenhum gatilho dispara e nenhuma ocorrência nova nasce até alguém ligar o laço.
 */
export function AvisoDoLacoDesligado() {
  return (
    <Banner tone="warning" icon={PowerOff} compact role="status" title="O laço de pedidos está desligado nesta instalação">
      Nenhum gatilho dispara e nenhuma ocorrência nova nasce. As datas mostradas são as previstas pela agenda. Para ligar,
      ponha <code>pedidos.enabled: true</code> na configuração e reinicie o backend.
    </Banner>
  );
}
