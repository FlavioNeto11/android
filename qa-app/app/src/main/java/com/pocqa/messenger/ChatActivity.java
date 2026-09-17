package com.pocqa.messenger;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

/** Conversa com um contato. O avanco de status fica na {@link StatusEngine} (nivel de processo). */
public class ChatActivity extends BaseActivity {

    private String account;
    private String contact;
    private EditText input;
    private ListView listView;
    private final MessageAdapter adapter = new MessageAdapter();
    private final Runnable dataListener = () -> reload(false);

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        account = Session.account(this);
        if (account == null) {
            goToLogin(false);
            return;
        }
        contact = getIntent().getStringExtra(Contract.EXTRA_CONTACT);
        if (contact == null || !Contract.CONTACTS.contains(contact)) {
            finish();
            return;
        }
        setContentView(R.layout.activity_chat);
        ((TextView) findViewById(R.id.chat_title)).setText(contact);
        ((TextView) findViewById(R.id.chat_account)).setText(getString(R.string.chat_account_fmt, account));
        findViewById(R.id.chat_back).setOnClickListener(v -> finish());

        input = findViewById(R.id.message_input);
        listView = findViewById(R.id.message_list);
        listView.setAdapter(adapter);
        findViewById(R.id.send_button).setOnClickListener(v -> send());
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!enforceSessionAndFlags()) {
            return;
        }
        if (listView == null) {
            return;
        }
        QaApp.addListener(dataListener);
        reload(true);
    }

    @Override
    protected void onPause() {
        QaApp.removeListener(dataListener);
        super.onPause();
    }

    private void send() {
        String body = input.getText().toString().trim();
        if (body.isEmpty()) {
            return; // entrada vazia ou so com espacos: nao faz nada
        }
        Db db = Db.get(this);
        long id = db.insertMessage(account, contact, body, Contract.STATUS_SENDING, System.currentTimeMillis());
        QaApp.engine(this).onMessageSent(id, contact);
        input.setText("");
        reload(true);
    }

    private void reload(boolean scrollToEnd) {
        adapter.setData(Db.get(this).conversation(account, contact));
        if (scrollToEnd && adapter.getCount() > 0) {
            listView.setSelection(adapter.getCount() - 1);
        }
    }

    private static final class MessageAdapter extends BaseAdapter {
        private List<Db.Message> items = new ArrayList<>();

        void setData(List<Db.Message> items) {
            this.items = items;
            notifyDataSetChanged();
        }

        @Override
        public int getCount() {
            return items.size();
        }

        @Override
        public Db.Message getItem(int position) {
            return items.get(position);
        }

        @Override
        public long getItemId(int position) {
            return items.get(position).id;
        }

        @Override
        public boolean hasStableIds() {
            return true;
        }

        @Override
        public boolean isEnabled(int position) {
            return false; // linhas nao clicaveis
        }

        @Override
        public boolean areAllItemsEnabled() {
            return false;
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            View row = convertView != null ? convertView
                    : LayoutInflater.from(parent.getContext()).inflate(R.layout.row_message, parent, false);
            Db.Message m = items.get(position);
            ((TextView) row.findViewById(R.id.message_text)).setText(m.body);
            ((TextView) row.findViewById(R.id.message_status)).setText(m.status);
            ((TextView) row.findViewById(R.id.message_time)).setText(Contract.hhmm(m.createdAt));
            return row;
        }
    }
}
