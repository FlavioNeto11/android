package com.pocqa.messenger;

import android.os.Bundle;
import android.view.View;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.TextView;

/** Perfil da conta logada, persistido na tabela profile. */
public class ProfileActivity extends BaseActivity {

    private String account;
    private EditText nameInput;
    private EditText emailInput;
    private EditText cityInput;
    private CheckBox notifications;
    private TextView savedBanner;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        account = Session.account(this);
        if (account == null) {
            goToLogin(false);
            return;
        }
        setContentView(R.layout.activity_profile);
        nameInput = findViewById(R.id.profile_name);
        emailInput = findViewById(R.id.profile_email);
        cityInput = findViewById(R.id.profile_city);
        notifications = findViewById(R.id.profile_notifications);
        savedBanner = findViewById(R.id.profile_saved_banner);
        // So o nome da conta (sem o prefixo "Conta: ") para nao se confundir com account_label da MainActivity.
        ((TextView) findViewById(R.id.profile_account)).setText(account);

        findViewById(R.id.profile_back).setOnClickListener(v -> finish());
        findViewById(R.id.profile_save).setOnClickListener(v -> save());

        if (savedInstanceState == null) {
            Db.Profile p = Db.get(this).profile(account);
            if (p != null) {
                nameInput.setText(p.name);
                emailInput.setText(p.email);
                cityInput.setText(p.city);
                notifications.setChecked(p.notifications);
            }
        }
    }

    private void save() {
        long now = System.currentTimeMillis();
        Db.get(this).saveProfile(account,
                nameInput.getText().toString().trim(),
                emailInput.getText().toString().trim(),
                cityInput.getText().toString().trim(),
                notifications.isChecked(), now);
        savedBanner.setText(getString(R.string.profile_saved_fmt, Contract.hhmm(now)));
        savedBanner.setVisibility(View.VISIBLE);
        QaApp.notifyDataChanged();
    }
}
