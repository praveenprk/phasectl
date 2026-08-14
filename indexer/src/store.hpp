// On-disk index store (M3, docs/internal/NATIVE_INDEXER_PLAN.md).
//
// SQLite-backed (C API — libsqlite3 only, no extra dependency), holding one
// `symbols` row per extracted definition. No extraction happens yet (that's
// M4); this milestone only opens/creates the database and its schema at the
// XDG cache path resolved by cache_path.hpp.

#pragma once

#include <filesystem>
#include <stdexcept>
#include <string>

#include <sqlite3.h>

namespace indexer {

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
