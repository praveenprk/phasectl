// XDG cache path resolution (M3, docs/internal/NATIVE_INDEXER_PLAN.md,
// "Resolved decisions" — index storage lives under $XDG_CACHE_HOME, never
// inside the indexed project's own tree).
//
// Layout: $XDG_CACHE_HOME/<binary-name>/<project-key>/index.db, default
// $XDG_CACHE_HOME to ~/.cache on any POSIX system (no macOS
// ~/Library/Caches special-casing — matches phasectl's own ~/.config
// convention).
//
// `<binary-name>` is "indexer" for now; the plan's own "Open questions"
// notes the real binary/project name isn't chosen yet (needed before M8
// packaging). Rename this one constant when that's settled.

#pragma once

#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <string>

namespace indexer {

inline constexpr const char* kBinaryName = "indexer";

// FNV-1a, 64-bit. Not cryptographic — just a stable, dependency-free way to
// turn an absolute project path into a short directory-safe key. Stable
// across processes/platforms/compilers, unlike std::hash<std::string>.
inline std::string project_key(const std::filesystem::path& project_path) {
    std::error_code ec;
    std::filesystem::path canonical =
        std::filesystem::weakly_canonical(project_path, ec);
    const std::string input = ec ? project_path.string() : canonical.string();

    constexpr std::uint64_t kOffsetBasis = 14695981039346656037ULL;
    constexpr std::uint64_t kPrime = 1099511628211ULL;

    std::uint64_t hash = kOffsetBasis;
    for (unsigned char c : input) {
        hash ^= c;
        hash *= kPrime;
    }

    static const char* kHex = "0123456789abcdef";
    std::string out(16, '0');
    for (int i = 15; i >= 0; --i) {
        out[static_cast<std::size_t>(i)] = kHex[hash & 0xF];
        hash >>= 4;
    }
    return out;
}

inline std::filesystem::path xdg_cache_home() {
    if (const char* env = std::getenv("XDG_CACHE_HOME");
        env != nullptr && env[0] != '\0') {
        return std::filesystem::path(env);
    }
    const char* home = std::getenv("HOME");
    return std::filesystem::path(home ? home : ".") / ".cache";
}

// Directory the index.db for `project_path` lives in. Does not create it.
inline std::filesystem::path index_dir_for(
    const std::filesystem::path& project_path) {
    return xdg_cache_home() / kBinaryName / project_key(project_path);
}

inline std::filesystem::path index_db_path(
    const std::filesystem::path& project_path) {
    return index_dir_for(project_path) / "index.db";
}

}  // namespace indexer
