// On-disk index store (M3, docs/internal/NATIVE_INDEXER_PLAN.md).
//
// SQLite-backed (C API — libsqlite3 only, no extra dependency), holding one
// `symbols` row per extracted definition (M4 onward — extract.hpp produces
// these) at the XDG cache path resolved by cache_path.hpp.

#pragma once

#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

#include <sqlite3.h>

namespace indexer {

struct Symbol {
    int id = 0;  // unset (0) for symbols not yet inserted; real id on search() results.
    std::string kind;
    std::string name;
    std::string qualified_name;
    std::string file_path;
    int start_line = 0;
    int end_line = 0;
    std::string signature;
};

class Store {
public:
    // Opens (creating if needed) the database at `db_path`, creating parent
    // directories as needed, and ensures the `symbols` schema exists.
    explicit Store(const std::filesystem::path& db_path) {
        std::error_code ec;
        std::filesystem::create_directories(db_path.parent_path(), ec);

        sqlite3* raw = nullptr;
        if (sqlite3_open(db_path.string().c_str(), &raw) != SQLITE_OK) {
            const std::string msg = raw ? sqlite3_errmsg(raw) : "unknown error";
            if (raw) sqlite3_close(raw);
            throw std::runtime_error("failed to open index db: " + msg);
        }
        db_ = raw;
        exec(
            "CREATE TABLE IF NOT EXISTS symbols ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  kind TEXT NOT NULL,"
            "  name TEXT NOT NULL,"
            "  qualified_name TEXT,"
            "  file_path TEXT NOT NULL,"
            "  start_line INTEGER,"
            "  end_line INTEGER,"
            "  signature TEXT"
            ");");
        exec("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);");
    }

    Store(const Store&) = delete;
    Store& operator=(const Store&) = delete;

    ~Store() {
        if (db_) sqlite3_close(db_);
    }

    sqlite3* handle() const { return db_; }

    // Drops every existing row. `index` calls this before re-inserting, so
    // re-running it is a full rebuild rather than an ever-growing append —
    // matches the contract's "index is idempotent" requirement.
    void clear() { exec("DELETE FROM symbols;"); }

    void insert_all(const std::vector<Symbol>& symbols) {
        exec("BEGIN TRANSACTION;");
        sqlite3_stmt* stmt = nullptr;
        const char* sql =
            "INSERT INTO symbols (kind, name, qualified_name, file_path, "
            "start_line, end_line, signature) VALUES (?, ?, ?, ?, ?, ?, ?);";
        if (sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            const std::string msg = sqlite3_errmsg(db_);
            exec("ROLLBACK;");
            throw std::runtime_error("failed to prepare insert: " + msg);
        }
        for (const auto& s : symbols) {
            sqlite3_reset(stmt);
            sqlite3_bind_text(stmt, 1, s.kind.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 2, s.name.c_str(), -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 3, s.qualified_name.c_str(), -1,
                               SQLITE_TRANSIENT);
            sqlite3_bind_text(stmt, 4, s.file_path.c_str(), -1,
                               SQLITE_TRANSIENT);
            sqlite3_bind_int(stmt, 5, s.start_line);
            sqlite3_bind_int(stmt, 6, s.end_line);
            sqlite3_bind_text(stmt, 7, s.signature.c_str(), -1,
                               SQLITE_TRANSIENT);
            if (sqlite3_step(stmt) != SQLITE_DONE) {
                const std::string msg = sqlite3_errmsg(db_);
                sqlite3_finalize(stmt);
                exec("ROLLBACK;");
                throw std::runtime_error("failed to insert symbol: " + msg);
            }
        }
        sqlite3_finalize(stmt);
        exec("COMMIT;");
    }

    // Case-insensitive substring match against `name`, ranked
    // exact-match-first (M5). `kind` empty means no kind filter.
    std::vector<Symbol> search(const std::string& term, const std::string& kind,
                                int limit) {
        std::string escaped;
        escaped.reserve(term.size());
        for (char c : term) {
            if (c == '%' || c == '_' || c == '\\') escaped.push_back('\\');
            escaped.push_back(c);
        }
        const std::string pattern = "%" + escaped + "%";

        const char* sql =
            "SELECT id, kind, name, qualified_name, file_path, start_line, "
            "end_line, signature FROM symbols "
            "WHERE name LIKE ?1 ESCAPE '\\' "
            "  AND (?2 = '' OR kind = ?2 COLLATE NOCASE) "
            "ORDER BY (name = ?3 COLLATE NOCASE) DESC, LENGTH(name) ASC, "
            "name COLLATE NOCASE ASC "
            "LIMIT ?4;";

        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            throw std::runtime_error(std::string("failed to prepare search: ") +
                                      sqlite3_errmsg(db_));
        }
        sqlite3_bind_text(stmt, 1, pattern.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 2, kind.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(stmt, 3, term.c_str(), -1, SQLITE_TRANSIENT);
        sqlite3_bind_int(stmt, 4, limit);

        std::vector<Symbol> results;
        int rc;
        while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
            Symbol s;
            s.id = sqlite3_column_int(stmt, 0);
            s.kind = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1));
            s.name = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2));
            const unsigned char* qn = sqlite3_column_text(stmt, 3);
            s.qualified_name = qn ? reinterpret_cast<const char*>(qn) : "";
            s.file_path =
                reinterpret_cast<const char*>(sqlite3_column_text(stmt, 4));
            s.start_line = sqlite3_column_int(stmt, 5);
            s.end_line = sqlite3_column_int(stmt, 6);
            const unsigned char* sig = sqlite3_column_text(stmt, 7);
            s.signature = sig ? reinterpret_cast<const char*>(sig) : "";
            results.push_back(std::move(s));
        }
        sqlite3_finalize(stmt);
        if (rc != SQLITE_DONE) {
            throw std::runtime_error(std::string("search failed: ") +
                                      sqlite3_errmsg(db_));
        }
        return results;
    }

private:
    void exec(const char* sql) {
        char* err = nullptr;
        if (sqlite3_exec(db_, sql, nullptr, nullptr, &err) != SQLITE_OK) {
            const std::string msg = err ? err : "unknown error";
            sqlite3_free(err);
            throw std::runtime_error("index db schema error: " + msg);
        }
    }

    sqlite3* db_ = nullptr;
};

}  // namespace indexer
