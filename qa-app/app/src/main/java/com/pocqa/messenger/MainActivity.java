package com.pocqa.messenger;

import android.content.Intent;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Lista de conversas (contatos fixos) com busca ao vivo. */
public class MainActivity extends BaseActivity {

    private String account;
    private TextView accountLabel;
    private EditText searchInput;
    private ListView listView;
    private View emptyView;
    private final ConversationAdapter adapter = new ConversationAdapter();
    private final Runnable dataListener = this::reload;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        account = Session.account(this);
        if (account == null) {
            goToLogin(false);
            return;
        }
        setContentView(R.layout.activity_main);
        accountLabel = findViewById(R.id.account_label);
        searchInput = findViewById(R.id.search_input);
        listView = findViewById(R.id.conversation_list);
        emptyView = findViewById(R.id.conversation_empty);

        listView.setAdapter(adapter);
        listView.setOnItemClickListener((parent, view, position, id) -> {
            Intent i = new Intent(this, ChatActivity.class);
            i.putExtra(Contract.EXTRA_CONTACT, adapter.getItem(position));
            startActivity(i);
        });

        findViewById(R.id.btn_profile).setOnClickListener(
                v -> startActivity(new Intent(this, ProfileActivity.class)));
        findViewById(R.id.btn_logout).setOnClickListener(v -> {
            Session.clear(this);
            goToLogin(false);
        });

        searchInput.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {
            }

            @Override
            public void onTextChanged(CharSequence s, int start, int before, int count) {
                reload();
            }

            @Override
            public void afterTextChanged(Editable s) {
            }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!enforceSessionAndFlags()) {
            return;
        }
        account = Session.account(this);
        QaApp.addListener(dataListener);
        reload();
    }

    @Override
    protected void onPause() {
        QaApp.removeListener(dataListener);
        super.onPause();
    }

    private void reload() {
        if (account == null || accountLabel == null) {
            return;
        }
        accountLabel.setText(getString(R.string.account_label_fmt, account));
        String q = searchInput.getText().toString().trim().toLowerCase(Locale.ROOT);
        List<String> filtered = new ArrayList<>();
        for (String contact : Contract.CONTACTS) {
            if (q.isEmpty() || contact.toLowerCase(Locale.ROOT).contains(q)) {
                filtered.add(contact);
            }
        }
        adapter.setData(filtered, Db.get(this).lastBodies(account));
        boolean empty = filtered.isEmpty();
        listView.setVisibility(empty ? View.GONE : View.VISIBLE);
        emptyView.setVisibility(empty ? View.VISIBLE : View.GONE);
    }

    private final class ConversationAdapter extends BaseAdapter {
        private List<String> contacts = new ArrayList<>();
        private Map<String, String> previews = new HashMap<>();

        void setData(List<String> contacts, Map<String, String> previews) {
            this.contacts = contacts;
            this.previews = previews;
            notifyDataSetChanged();
        }

        @Override
        public int getCount() {
            return contacts.size();
        }

        @Override
        public String getItem(int position) {
            return contacts.get(position);
        }

        @Override
        public long getItemId(int position) {
            return Contract.CONTACTS.indexOf(contacts.get(position));
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            View row = convertView != null ? convertView
                    : LayoutInflater.from(parent.getContext()).inflate(R.layout.row_conversation, parent, false);
            String contact = contacts.get(position);
            String preview = previews.get(contact);
            ((TextView) row.findViewById(R.id.conversation_avatar)).setText(contact.substring(0, 1));
            ((TextView) row.findViewById(R.id.conversation_name)).setText(contact);
            ((TextView) row.findViewById(R.id.conversation_preview)).setText(
                    preview == null ? getString(R.string.no_messages) : preview);
            return row;
        }
    }
}
