// File discovery (M2, docs/internal/NATIVE_INDEXER_PLAN.md).
//
// Walks a project tree, skipping .git and a small vendor/ignore list, and
// returns paths whose extension is in the source-file allowlist. No
// storage, no parsing — just the file list.

#pragma once

#include <filesystem>
#include <string>
#include <unordered_set>
#include <vector>

namespace indexer {

// Directory names never descended into, wherever they appear in the tree.
inline const std::unordered_set<std::string>& ignored_dir_names() {
    static const std::unordered_set<std::string> names = {
        ".git", "node_modules", ".venv", "__pycache__", "dist", "build",
        ".codegraph",
    };
    return names;
}

// Extensions considered source files. Broad on purpose for M2 (plain file
// discovery); M4 narrows to what's actually parsed, starting with C++.
inline const std::unordered_set<std::string>& source_extensions() {
    static const std::unordered_set<std::string> exts = {
        ".c", ".h", ".cc", ".hh", ".cpp", ".hpp", ".cxx", ".hxx",
        ".py", ".js", ".jsx", ".mjs", ".ts", ".tsx",
        ".go", ".rs", ".java", ".rb", ".cs", ".swift", ".kt", ".m", ".mm",
        ".php", ".scala", ".sh",
    };
    return exts;
}

inline std::vector<std::filesystem::path> discover_source_files(
    const std::filesystem::path& root) {
    std::vector<std::filesystem::path> files;

    std::error_code ec;
    std::filesystem::recursive_directory_iterator it(
        root, std::filesystem::directory_options::skip_permission_denied, ec);
    std::filesystem::recursive_directory_iterator end;

    for (; !ec && it != end; it.increment(ec)) {
        const auto& entry = *it;

        if (entry.is_directory(ec)) {
            if (ignored_dir_names().count(entry.path().filename().string())) {
                it.disable_recursion_pending();
            }
            continue;
        }

        if (!entry.is_regular_file(ec)) {
            continue;
        }

        if (source_extensions().count(entry.path().extension().string())) {
            files.push_back(entry.path());
        }
    }

    return files;
}

}  // namespace indexer
