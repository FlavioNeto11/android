package com.pocqa.messenger;

import android.app.Activity;
import android.app.Dialog;
import android.content.Intent;

/** Comportamento comum: exigencia de sessao e flags de injecao de falha avaliadas em onResume. */
abstract class BaseActivity extends Activity {

    private Dialog interstitial;

    /**
     * Deve ser chamado em onResume de MainActivity e ChatActivity.
     * Ordem: sessao ausente, depois force_reauth, depois interstitial.
     *
     * @return false se a tela foi redirecionada para o login (a activity ja chamou finish()).
     */
    protected boolean enforceSessionAndFlags() {
        if (Session.account(this) == null) {
            goToLogin(false);
            return false;
        }
        Db db = Db.get(this);
        if (db.flagOn(Contract.FLAG_FORCE_REAUTH)) {
            Session.clear(this);
            db.setFlag(Contract.FLAG_FORCE_REAUTH, "0");
            goToLogin(true);
            return false;
        }
        if (db.flagOn(Contract.FLAG_INTERSTITIAL)) {
            showInterstitial();
        } else {
            hideInterstitial();
        }
        return true;
    }

    protected void goToLogin(boolean sessionExpired) {
        hideInterstitial();
        Intent i = new Intent(this, LoginActivity.class);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        if (sessionExpired) {
            i.putExtra(Contract.EXTRA_NOTICE, Contract.NOTICE_EXPIRED);
        }
        startActivity(i);
        finish();
    }

    /** Janela em tela cheia, nao cancelavel (Voltar nao fecha): bloqueia a tela ate tocar em "Agora nao". */
    private void showInterstitial() {
        if (interstitial != null && interstitial.isShowing()) {
            return;
        }
        Dialog d = new Dialog(this, R.style.AppTheme);
        d.setContentView(R.layout.dialog_interstitial);
        d.setCancelable(false);
        d.setCanceledOnTouchOutside(false);
        d.findViewById(R.id.interstitial_dismiss).setOnClickListener(v -> {
            Db.get(this).setFlag(Contract.FLAG_INTERSTITIAL, "0");
            hideInterstitial();
        });
        interstitial = d;
        d.show();
    }

    private void hideInterstitial() {
        if (interstitial != null) {
            if (interstitial.isShowing()) {
                interstitial.dismiss();
            }
            interstitial = null;
        }
    }

    @Override
    protected void onDestroy() {
        hideInterstitial();
        super.onDestroy();
    }
}
