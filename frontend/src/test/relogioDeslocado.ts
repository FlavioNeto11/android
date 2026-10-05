// Setup só da varredura de datas fixas (29.150, `DESLOCAMENTO_DIAS=40 npm test`): desloca o relógio do teste por
// DESLOCAMENTO_DIAS (negativo atrasa, fracionário vale). Sem a variável, não faz nada.
//
// Para que serve: o teste que usa uma data fixa como "futuro" (o `expires_at` de 05/10 21:00Z do PortaDoPlano, 29.149)
// passa no dia em que é escrito e quebra quando o relógio passa dela, em qualquer ramo. Rodando a suíte com o relógio
// à frente (+2, +40, +400 dias) o teste assim aparece antes. Para TRÁS, os testes com fixtures em setembro falham
// (o "passado" fixo vira futuro): isso não é defeito, o relógio de verdade só anda para a frente.
const dias = Number(process.env.DESLOCAMENTO_DIAS ?? 0);

if (Number.isFinite(dias) && dias !== 0) {
  const Real = Date;
  const desloque = dias * 864e5;
  class Deslocada extends Real {
    constructor(...args: unknown[]) {
      if (args.length === 0) super(Real.now() + desloque);
      else super(...(args as [number]));
    }

    static override now(): number {
      return Real.now() + desloque;
    }
  }
  (globalThis as unknown as { Date: DateConstructor }).Date = Deslocada as unknown as DateConstructor;
}
