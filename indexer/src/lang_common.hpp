// Helpers shared by the naive, line-based extractors for brace-delimited
// languages (extract_cpp.hpp, extract_js.hpp). Split out in M7
// (docs/internal/NATIVE_INDEXER_PLAN.md) when JS extraction needed the
// same comment-stripping/brace-matching logic C++ already had — kept
// identical between languages rather than forked, since forking it would
// just be two copies to keep in sync for zero behavioral gain.

#pragma once

#include <cctype>
#include <stack>
#include <string>
#include <unordered_map>

namespace indexer {
namespace detail {

// Blanks out `//` and `/* */` comments, preserving newlines and overall
// character offsets so line numbers computed later stay accurate. C-style
// comments are shared syntax between C++ and JS/TS.
inline std::string strip_comments(const std::string& src) {
    std::string out;
    out.reserve(src.size());
    bool in_line_comment = false;
    bool in_block_comment = false;
    for (std::size_t i = 0; i < src.size(); ++i) {
        char c = src[i];
        char next = (i + 1 < src.size()) ? src[i + 1] : '\0';
        if (in_line_comment) {
            if (c == '\n') {
                in_line_comment = false;
                out.push_back(c);
            } else {
                out.push_back(' ');
            }
            continue;
        }
        if (in_block_comment) {
            if (c == '*' && next == '/') {
                in_block_comment = false;
                out.push_back(' ');
                out.push_back(' ');
                ++i;
            } else {
                out.push_back(c == '\n' ? '\n' : ' ');
            }
            continue;
        }
        if (c == '/' && next == '/') {
            in_line_comment = true;
            out.push_back(' ');
            out.push_back(' ');
            ++i;
            continue;
        }
        if (c == '/' && next == '*') {
            in_block_comment = true;
            out.push_back(' ');
            out.push_back(' ');
            ++i;
            continue;
        }
        out.push_back(c);
    }
    return out;
}

// Maps the line of every `{` to the line of its matching `}`.
inline std::unordered_map<int, int> match_braces(const std::string& text) {
    std::unordered_map<int, int> close_for_open_line;
    std::stack<int> open_lines;
    int line = 1;
    for (char c : text) {
        if (c == '{') {
            open_lines.push(line);
        } else if (c == '}') {
            if (!open_lines.empty()) {
                close_for_open_line[open_lines.top()] = line;
                open_lines.pop();
            }
        } else if (c == '\n') {
            ++line;
        }
    }
    return close_for_open_line;
}

inline std::string trim(const std::string& s) {
    std::size_t start = s.find_first_not_of(" \t\r");
    if (start == std::string::npos) return "";
    std::size_t end = s.find_last_not_of(" \t\r");
    return s.substr(start, end - start + 1);
}

// Scans backward from the end of `head` for a `[\w:$]`-shaped token — the
// candidate function/method name (possibly qualified with `Class::` in
// C++). Returns "" if the head doesn't end in such a token (e.g. C++
// operator overloads, whose last character before `(` isn't a word char).
// `$` is included for JS identifiers, harmless for C++ (not a valid C++
// identifier character, so it never appears in real C++ source).
inline std::string trailing_identifier(const std::string& head) {
    std::size_t end = head.size();
    while (end > 0 &&
           std::isspace(static_cast<unsigned char>(head[end - 1]))) {
        --end;
    }
    std::size_t start = end;
    auto is_ident_char = [](char c) {
        return std::isalnum(static_cast<unsigned char>(c)) || c == '_' ||
               c == ':' || c == '$';
    };
    while (start > 0 && is_ident_char(head[start - 1])) {
        --start;
    }
    if (start == end) return "";
    return head.substr(start, end - start);
}

}  // namespace detail
}  // namespace indexer
