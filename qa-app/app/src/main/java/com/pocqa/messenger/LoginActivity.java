package com.pocqa.messenger;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

/**
 * Login com contas FICTICIAS (qa-user-01 a qa-user-10, PIN 1234).
 * Provisionamento para testes: extras de intent "account" e "pin" fazem login automatico, p.ex.
 *   adb shell am start -n com.pocqa.messenger/.LoginActivity --es account qa-user-03 --es pin 1234
 */
public class LoginActivity extends Activity {

    private EditText accountInput;
    private EditText pinInput;
    private TextView errorView;
    private TextView noticeView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_login);
        accountInput = findViewById(R.id.login_account);
        pinInput = findViewById(R.id.login_pin);
        errorView = findViewById(R.id.login_error);
        noticeView = findViewById(R.id.login_notice);
        Button loginButton = findViewById(R.id.login_button);

        loginButton.setOnClickListener(v -> submitForm());
        pinInput.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                submitForm();
                return true;
            }
            return false;
        });

        handleIntent(getIntent(), savedInstanceState == null);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent, true);
    }

    private void handleIntent(Intent intent, boolean allowAutoLogin) {
        boolean expired = intent != null
                && Contract.NOTICE_EXPIRED.equals(intent.getStringExtra(Contract.EXTRA_NOTICE));
        noticeView.setVisibility(expired ? View.VISIBLE : View.GONE);

        if (!allowAutoLogin || intent == null
                || !intent.hasExtra(Contract.EXTRA_ACCOUNT) || !intent.hasExtra(Contract.EXTRA_PIN)) {
            return;
        }
        String account = intent.getStringExtra(Contract.EXTRA_ACCOUNT);
        String pin = intent.getStringExtra(Contract.EXTRA_PIN);
        if (pin == null) {
            // Tolerancia a "--ei pin 1234" no lugar de "--es pin 1234".
            int n = intent.getIntExtra(Contract.EXTRA_PIN, -1);
            pin = n >= 0 ? String.valueOf(n) : "";
        }
        if (!attemptLogin(account, pin)) {
            accountInput.setText(account == null ? "" : account);
        }
    }

    private void submitForm() {
        attemptLogin(accountInput.getText().toString(), pinInput.getText().toString());
    }

    private boolean attemptLogin(String rawAccount, String pin) {
        String account = Contract.normalizeAccount(rawAccount);
        if (!Contract.credentialsValid(account, pin)) {
            errorView.setText(R.string.login_error);
            errorView.setVisibility(View.VISIBLE);
            pinInput.setText("");
            return false;
        }
        errorView.setVisibility(View.GONE);
        Session.login(this, account);
        Intent i = new Intent(this, MainActivity.class);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        startActivity(i);
        finish();
        return true;
    }
}
