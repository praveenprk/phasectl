// Native C++ code indexer — CLI skeleton (M1, docs/internal/NATIVE_INDEXER_PLAN.md).
//
// Speaks the plain-subprocess contract phasectl already expects from CodeGraph:
//   indexer index <path>
//   indexer query <search> [--path <dir>] [--json] [--limit N] [--kind K]
//
// M4 adds symbol extraction for one source language (C++, self-hosted
// dogfooding — see extract_cpp.hpp): `index` extracts symbols from
// discovered .cpp/.h/.hpp/.cc/... files and stores them. Other discovered
// files are still counted but not yet parsed (later languages: M7).
//
// M5 wires `query` up to Store::search: case-insensitive substring match
// against `name`, ranked exact-match-first. --json output is shaped like
// CodeGraph's `{node: {...}, score}` (see docs/internal/NATIVE_INDEXER_PLAN.md,
// "Contract to match") with a documented, deliberately smaller field set —
// this indexer has no language/visibility/isExported/etc. metadata to report.
//
// M6 adds `status <path> --json`: fileCount/nodeCount (plus a kind
// breakdown) so `phasectl index`'s _summarize_index picks it up for free
// (src/phasectl/cli.py). Always exits 0 — an unindexed path just reports
// initialized:false, same as index/query report their own errors
// separately rather than status being the thing that fails.
//
// M7 adds two more source languages beyond C++'s self-hosted dogfood:
// Python (extract_python.hpp) and JavaScript/TypeScript (extract_js.hpp).
// `index` now dispatches each discovered file to whichever extractor
// matches its extension; files whose extension has no extractor are
// still counted (M2) but contribute no symbols.

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

#include "cache_path.hpp"
#include "discover.hpp"
#include "extract_cpp.hpp"
#include "extract_js.hpp"
#include "extract_python.hpp"
#include "store.hpp"

namespace {

void usage(std::ostream& out) {
    out << "usage: indexer index <path>\n"
        << "       indexer query <search> [--path <dir>] [--json] [--limit N] [--kind K]\n"
        << "       indexer status <path> [--json]\n";
}

struct QueryOptions {
    std::string search;
    std::string path;
    std::string kind;
    int limit = 10;
    bool json = false;
};

std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

int run_index(const std::vector<std::string>& args) {
    if (args.empty()) {
        std::cerr << "error: index requires a <path>\n";
        usage(std::cerr);
        return 1;
    }

    const std::filesystem::path root = args.front();
    std::error_code ec;
    if (!std::filesystem::exists(root, ec) || ec) {
        std::cerr << "error: path does not exist: " << root.string() << "\n";
        return 1;
    }

    const auto files = indexer::discover_source_files(root);

    static const std::unordered_set<std::string> kCppExtensions = {
        ".c", ".h", ".cc", ".hh", ".cpp", ".hpp", ".cxx", ".hxx",
    };
    static const std::unordered_set<std::string> kPythonExtensions = {".py"};
    static const std::unordered_set<std::string> kJsExtensions = {
        ".js", ".jsx", ".mjs", ".ts", ".tsx",
    };

    std::vector<indexer::Symbol> symbols;
    for (const auto& file : files) {
        const std::string ext = file.extension().string();
        const bool is_cpp = kCppExtensions.count(ext) != 0;
        const bool is_python = kPythonExtensions.count(ext) != 0;
        const bool is_js = kJsExtensions.count(ext) != 0;
        if (!is_cpp && !is_python && !is_js) continue;

        std::ifstream in(file, std::ios::binary);
        if (!in) continue;
        std::ostringstream buf;
        buf << in.rdbuf();

        std::filesystem::path abs = std::filesystem::weakly_canonical(file, ec);
        const std::string file_path = ec ? file.string() : abs.string();
        const std::string content = buf.str();

        std::vector<indexer::Symbol> extracted;
        if (is_cpp) {
            extracted = indexer::extract_cpp_symbols(file_path, content);
        } else if (is_python) {
            extracted = indexer::extract_python_symbols(file_path, content);
        } else {
            extracted = indexer::extract_js_symbols(file_path, content);
        }
        symbols.insert(symbols.end(),
                        std::make_move_iterator(extracted.begin()),
                        std::make_move_iterator(extracted.end()));
    }

    const std::filesystem::path db_path = indexer::index_db_path(root);
    try {
        indexer::Store store(db_path);
        store.clear();
        store.insert_all(symbols);
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }

    std::cerr << "indexed " << files.size() << " file"
               << (files.size() == 1 ? "" : "s") << ", " << symbols.size()
               << " symbol" << (symbols.size() == 1 ? "" : "s") << " under "
               << root.string() << " (db: " << db_path.string() << ")\n";
    return 0;
}

int run_query(const std::vector<std::string>& args) {
    QueryOptions opts;
    bool have_search = false;

    for (std::size_t i = 0; i < args.size(); ++i) {
        const std::string& arg = args[i];
        if (arg == "--json") {
            opts.json = true;
        } else if (arg == "--path" || arg == "-p") {
            if (i + 1 >= args.size()) {
                std::cerr << "error: " << arg << " requires a value\n";
                return 1;
            }
            opts.path = args[++i];
        } else if (arg == "--limit" || arg == "-l") {
            if (i + 1 >= args.size()) {
                std::cerr << "error: " << arg << " requires a value\n";
                return 1;
            }
            opts.limit = std::stoi(args[++i]);
        } else if (arg == "--kind" || arg == "-k") {
            if (i + 1 >= args.size()) {
                std::cerr << "error: " << arg << " requires a value\n";
                return 1;
            }
            opts.kind = args[++i];
        } else if (!have_search) {
            opts.search = arg;
            have_search = true;
        } else {
            std::cerr << "error: unexpected argument '" << arg << "'\n";
            return 1;
        }
    }

    if (!have_search) {
        std::cerr << "error: query requires a <search> term\n";
        usage(std::cerr);
        return 1;
    }

    const std::filesystem::path root = opts.path.empty() ? "." : opts.path;
    const std::filesystem::path db_path = indexer::index_db_path(root);
    std::error_code ec;
    if (!std::filesystem::exists(db_path, ec)) {
        std::cerr << "error: no index for " << root.string() << " — run "
                   << "'indexer index " << root.string() << "' first\n";
        return 1;
    }

    std::vector<indexer::Symbol> results;
    try {
        indexer::Store store(db_path);
        results = store.search(opts.search, opts.kind, opts.limit);
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }

    if (opts.json) {
        std::cout << "[";
        for (std::size_t i = 0; i < results.size(); ++i) {
            const auto& s = results[i];
            const bool exact =
                s.name.size() == opts.search.size() &&
                std::equal(s.name.begin(), s.name.end(), opts.search.begin(),
                           [](char a, char b) {
                               return std::tolower(static_cast<unsigned char>(a)) ==
                                      std::tolower(static_cast<unsigned char>(b));
                           });
            std::cout << (i == 0 ? "" : ",") << "{\"node\":{"
                       << "\"id\":" << s.id << ","
                       << "\"kind\":\"" << json_escape(s.kind) << "\","
                       << "\"name\":\"" << json_escape(s.name) << "\","
                       << "\"qualifiedName\":\"" << json_escape(s.qualified_name) << "\","
                       << "\"filePath\":\"" << json_escape(s.file_path) << "\","
                       << "\"startLine\":" << s.start_line << ","
                       << "\"endLine\":" << s.end_line << ","
                       << "\"signature\":\"" << json_escape(s.signature) << "\""
                       << "},\"score\":" << (exact ? "1.0" : "0.5") << "}";
        }
        std::cout << "]\n";
    } else {
        for (const auto& s : results) {
            std::cout << s.kind << "  " << s.qualified_name << "\n"
                       << "  " << s.file_path << ":" << s.start_line << "-"
                       << s.end_line << "\n"
                       << "  " << s.signature << "\n\n";
        }
    }
    return 0;
}

int run_status(const std::vector<std::string>& args) {
    std::string path;
    bool json = false;
    bool have_path = false;

    for (const auto& arg : args) {
        if (arg == "--json") {
            json = true;
        } else if (!have_path) {
            path = arg;
            have_path = true;
        } else {
            std::cerr << "error: unexpected argument '" << arg << "'\n";
            return 1;
        }
    }

    if (!have_path) {
        std::cerr << "error: status requires a <path>\n";
        usage(std::cerr);
        return 1;
    }

    const std::filesystem::path root = path;
    const std::filesystem::path db_path = indexer::index_db_path(root);
    std::error_code ec;
    const bool initialized = std::filesystem::exists(db_path, ec);

    int file_count = 0;
    int node_count = 0;
    std::vector<std::pair<std::string, int>> kinds;
    if (initialized) {
        try {
            indexer::Store store(db_path);
            file_count = store.file_count();
            node_count = store.node_count();
            kinds = store.kind_counts();
        } catch (const std::exception& e) {
            std::cerr << "error: " << e.what() << "\n";
            return 1;
        }
    }

    if (json) {
        std::cout << "{\"initialized\":" << (initialized ? "true" : "false")
                   << ",\"projectPath\":\"" << json_escape(root.string()) << "\""
                   << ",\"indexPath\":\"" << json_escape(db_path.string()) << "\""
                   << ",\"fileCount\":" << file_count
                   << ",\"nodeCount\":" << node_count
                   << ",\"backend\":\"sqlite\""
                   << ",\"nodesByKind\":{";
        for (std::size_t i = 0; i < kinds.size(); ++i) {
            std::cout << (i == 0 ? "" : ",") << "\"" << json_escape(kinds[i].first)
                       << "\":" << kinds[i].second;
        }
        std::cout << "}}\n";
    } else {
        std::cout << "initialized: " << (initialized ? "yes" : "no") << "\n";
        if (initialized) {
            std::cout << "files:  " << file_count << "\n"
                       << "nodes:  " << node_count << "\n"
                       << "index:  " << db_path.string() << "\n";
            for (const auto& [kind, count] : kinds) {
                std::cout << "  " << kind << ": " << count << "\n";
            }
        }
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        usage(std::cerr);
        return 1;
    }

    const std::string command = argv[1];
    const std::vector<std::string> args(argv + 2, argv + argc);

    if (command == "index") {
        return run_index(args);
    }
    if (command == "query") {
        return run_query(args);
    }
    if (command == "status") {
        return run_status(args);
    }

    std::cerr << "error: unknown command '" << command << "'\n";
    usage(std::cerr);
    return 1;
}
