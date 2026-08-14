// Naive Python symbol extraction (M7, docs/internal/NATIVE_INDEXER_PLAN.md).
// Same accepted limitations as extract_cpp.hpp — regex/line-based, not an
// AST — but Python has no braces, so scoping is indentation-based instead
// of brace-matched: a scope (def/class body) ends at the last line before
// one whose indentation is <= the scope header's own indentation.
//
// Detects:
//   - `def name(...):` / `async def name(...):` — signature may continue
//     past this line (multi-line arg lists are fine, only the `def` line
//     itself needs to match); tagged "method" if nested directly inside a
//     class body, "function" otherwise.
//   - `class Name:` / `class Name(Base):`.
// Misses: decorators aren't attached to the symbol they decorate (each is
// just an ordinary line); nested functions still work (indentation-based
// scoping handles arbitrary nesting) but a `def` nested inside another
// `def` is tagged "function", not something like "closure" — no kind for
// that distinction.

#pragma once

#include <regex>
#include <string>
#include <vector>

#include "lang_common.hpp"
#include "store.hpp"

namespace indexer {

inline std::vector<Symbol> extract_python_symbols(const std::string& file_path,
                                                    const std::string& source) {
    std::vector<Symbol> symbols;

    static const std::regex def_re(
        R"(^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\()");
    static const std::regex class_re(R"(^(\s*)class\s+([A-Za-z_]\w*)\b)");

    struct Frame {
        int indent;
        std::string kind;
        std::string qualified_name;
        int start_line;
    };
    std::vector<Frame> stack;

    std::vector<std::string> lines;
    {
        std::size_t pos = 0;
        while (pos <= source.size()) {
            std::size_t nl = source.find('\n', pos);
            lines.push_back(nl == std::string::npos ? source.substr(pos)
                                                      : source.substr(pos, nl - pos));
            if (nl == std::string::npos) break;
            pos = nl + 1;
        }
    }

    int last_code_line = 0;

    auto close_frame = [&](const Frame& f, int end_line) {
        Symbol s;
        s.kind = f.kind;
        std::size_t dot = f.qualified_name.rfind('.');
        s.name = dot == std::string::npos ? f.qualified_name
                                           : f.qualified_name.substr(dot + 1);
        s.qualified_name = f.qualified_name;
        s.file_path = file_path;
        s.start_line = f.start_line;
        s.end_line = end_line;
        s.signature = detail::trim(lines[static_cast<std::size_t>(f.start_line - 1)]);
        symbols.push_back(std::move(s));
    };

    for (std::size_t idx = 0; idx < lines.size(); ++idx) {
        const int line_no = static_cast<int>(idx) + 1;
        const std::string& raw_line = lines[idx];
        const std::string trimmed = detail::trim(raw_line);

        if (trimmed.empty() || trimmed[0] == '#') continue;

        const std::size_t indent = raw_line.find_first_not_of(" \t");
        const int indent_i = static_cast<int>(indent == std::string::npos ? 0 : indent);

        while (!stack.empty() && stack.back().indent >= indent_i) {
            close_frame(stack.back(), last_code_line);
            stack.pop_back();
        }
        last_code_line = line_no;

        std::smatch m;
        if (std::regex_search(raw_line, m, class_re)) {
            const std::string qualifier =
                stack.empty() ? "" : stack.back().qualified_name;
            const std::string qualified_name =
                qualifier.empty() ? m[2].str() : qualifier + "." + m[2].str();
            stack.push_back({indent_i, "class", qualified_name, line_no});
        } else if (std::regex_search(raw_line, m, def_re)) {
            const bool in_class = !stack.empty() && stack.back().kind == "class";
            const std::string qualifier =
                stack.empty() ? "" : stack.back().qualified_name;
            const std::string qualified_name =
                qualifier.empty() ? m[2].str() : qualifier + "." + m[2].str();
            stack.push_back(
                {indent_i, in_class ? "method" : "function", qualified_name, line_no});
        }
    }

    while (!stack.empty()) {
        close_frame(stack.back(), last_code_line);
        stack.pop_back();
    }

    return symbols;
}

}  // namespace indexer
