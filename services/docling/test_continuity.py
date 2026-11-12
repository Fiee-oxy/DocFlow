import copy
import unittest

from continuity import can_defer, merge_boundary, source_fragments


def prose(text, page, *, section="Chapter 1", top=None, bottom=None, label="text"):
    return {"text": text, "pages": [page], "section": section, "ref": "#/texts/1",
            "label": label,
            "bbox": {"l": 10, "t": (720 if page == 1 else 30) if top is None else top,
                     "r": 580, "b": (780 if page == 1 else 100) if bottom is None else bottom,
                     "coord_origin": "TOPLEFT"},
            "page_size": {"width": 600, "height": 800}}


def table(page, header=("Name", "Value"), value="12"):
    cells = []
    for row, values in enumerate((header, ("Revenue", value))):
        for col, text in enumerate(values):
            cells.append({"text": text, "start_row_offset_idx": row, "end_row_offset_idx": row + 1,
                          "start_col_offset_idx": col, "end_col_offset_idx": col + 1,
                          "column_header": row == 0, "bbox": {"l": 10, "t": 10, "r": 20, "b": 20}})
    block = prose(f"| {header[0]} | {header[1]} |\n| --- | --- |\n| Revenue | {value} |", page, label="table")
    block["table_data"] = {"num_rows": 2, "num_cols": 2, "table_cells": cells}
    return block


class ContinuityTests(unittest.TestCase):
    def test_chinese_adjacent_page_continuation_and_exact_sources(self):
        a, b = prose("学生应修满", 1, section="正文"), prose("一百六十学分。", 2, section="正文")
        original = copy.deepcopy((a, b))
        merged = merge_boundary(a, b)
        self.assertEqual(merged["text"], "学生应修满一百六十学分。")
        self.assertEqual(merged["pages"], [1, 2])
        self.assertEqual([part["text"] for part in merged["source_fragments"]], [a["text"], b["text"]])
        self.assertNotEqual(merged["source_fragments"][0]["origin_id"], merged["source_fragments"][1]["origin_id"])
        self.assertEqual((a, b), original)
        self.assertNotIn("bbox", merged)

    def test_english_continuation_and_hyphen(self):
        self.assertEqual(merge_boundary(prose("Students must complete", 1),
                                        prose("all required modules.", 2))["text"],
                         "Students must complete all required modules.")
        result = merge_boundary(prose("An inter-", 1), prose("national programme.", 2))
        self.assertEqual(result["text"], "An international programme.")
        self.assertTrue(result["continuity"]["dehyphenated"])
        self.assertEqual(result["source_fragments"][0]["text"], "An inter-")

    def test_terminal_punctuation_capitalization_and_lists_do_not_merge(self):
        for ending in ("complete.", "完成。", "complete!", "完成；", "Complete.”", "Requirements:"):
            with self.subTest(ending=ending):
                self.assertIsNone(merge_boundary(prose(ending, 1), prose("continued text", 2)))
        for beginning in ("New paragraph", "1. requirement", "（一）要求", "- another point", "第三章 内容", "# Heading"):
            with self.subTest(beginning=beginning):
                self.assertIsNone(merge_boundary(prose("unfinished", 1), prose(beginning, 2)))

    def test_section_label_geometry_and_page_guards(self):
        left, right = prose("unfinished", 1), prose("continued", 2)
        variants = [{"section": "Other"}, {"label": "section_header"}, {"pages": [3]},
                    {"bbox": None}, {"page_size": None}, {"layout_ambiguous": True}]
        for patch in variants:
            with self.subTest(patch=patch):
                self.assertIsNone(merge_boundary(left, {**right, **patch}))
        self.assertIsNone(merge_boundary(prose("unfinished", 1, bottom=400), right))
        self.assertIsNone(merge_boundary(left, prose("continued", 2, top=400, bottom=500)))
        self.assertIsNone(merge_boundary({**left, "bbox": {**left["bbox"], "coord_origin": "UNKNOWN"}}, right))

    def test_bottom_left_coordinates(self):
        left, right = prose("unfinished", 1), prose("continued", 2)
        for block in (left, right):
            box = block["bbox"]
            box.update(t=800 - box["t"], b=800 - box["b"], coord_origin="BOTTOMLEFT")
        self.assertIsNotNone(merge_boundary(left, right))

    def test_carry_can_merge_again_and_fragment_geometry_is_used(self):
        first = merge_boundary(prose("this is", 1), prose("a long", 2, bottom=780))
        third = prose("paragraph.", 3)
        merged = merge_boundary(first, third)
        self.assertEqual(merged["text"], "this is a long paragraph.")
        self.assertEqual(len(merged["source_fragments"]), 3)
        self.assertEqual(merged["pages"], [1, 2, 3])

    def test_fragment_limit_bounds_progressive_carry(self):
        merged = prose("a", 1)
        for page in range(2, 17):
            merged = merge_boundary(merged, prose("b", page, top=30, bottom=780))
            self.assertIsNotNone(merged)
        self.assertEqual(len(merged["source_fragments"]), 16)
        self.assertIsNone(merge_boundary(merged, prose("c", 17)))
        self.assertFalse(can_defer(merged))
        self.assertIsNone(merge_boundary(prose("a", 1), prose("b", 2), max_fragments=1))

    def test_three_page_table_uses_tail_geometry_and_preserves_all_original_cells(self):
        left, middle, right = table(1), table(2, value="13"), table(3, value="14")
        middle["bbox"]["b"] = 780
        merged = merge_boundary(merge_boundary(left, middle), right)
        self.assertIsNotNone(merged)
        self.assertEqual(merged["pages"], [1, 2, 3])
        self.assertEqual(merged["table_data"]["num_rows"], 4)
        self.assertEqual(len(merged["table_data"]["table_cells"]), 8)
        self.assertEqual(sum(len(part["table_data"]["table_cells"])
                             for part in merged["source_fragments"]), 12)
        self.assertEqual(merged["text"].count("| Name | Value |"), 1)
        middle["bbox"]["b"] = 300
        self.assertIsNone(merge_boundary(merge_boundary(left, middle), right))

    def test_can_defer_agrees_with_merge_geometry_and_completed_sentence_rules(self):
        self.assertTrue(can_defer(prose("unfinished", 1)))
        self.assertFalse(can_defer(prose("finished.", 1)))
        self.assertFalse(can_defer(prose("完成。", 1)))
        self.assertFalse(can_defer({**prose("unfinished", 1), "layout_ambiguous": True}))
        block = prose("unfinished", 1)
        block["bbox"]["coord_origin"] = "UNKNOWN"
        self.assertFalse(can_defer(block))
        block["bbox"].update(coord_origin="TOPLEFT", b=float("nan"))
        self.assertFalse(can_defer(block))

    def test_repeated_table_headers_merge_without_losing_source_cells(self):
        left, right = table(1), table(2, value="13")
        result = merge_boundary(left, right)
        self.assertIsNotNone(result)
        self.assertEqual(result["table_data"]["num_rows"], 3)
        self.assertEqual(len(result["table_data"]["table_cells"]), 6)
        self.assertEqual(result["text"].count("| Name | Value |"), 1)
        self.assertEqual(result["source_fragments"][1]["table_data"], right["table_data"])
        self.assertTrue(result["continuity"]["display_header_removed"])
        self.assertEqual(result["continuity"]["repeated_header_rows"], 1)
        self.assertFalse(any("bbox" in cell for cell in result["table_data"]["table_cells"]))

    def test_same_columns_alone_does_not_prove_table_continuity(self):
        for header in (("Other", "Value"), ("Value", "Name"), ("", "")):
            self.assertIsNone(merge_boundary(table(1), table(2, header=header)))
        self.assertIsNone(merge_boundary({**table(1), "bbox": None}, table(2)))
        self.assertIsNone(merge_boundary({**table(1), "section": "Other"}, table(2)))

    def test_explicit_continuation_caption_still_needs_matching_headers(self):
        left, right = table(1), table(2)
        left.update(caption="Table 2. Revenue", bbox=None)
        right.update(caption="Table 2. Revenue (continued)", bbox=None)
        self.assertIsNotNone(merge_boundary(left, right))
        right["caption"] = "Table 3. Revenue (continued)"
        self.assertIsNone(merge_boundary(left, right))
        left, right = table(1), table(2)
        right["caption"] = "Table 3. Revenue"
        self.assertIsNone(merge_boundary(left, right))
        left, right = table(1), table(2)
        left["caption"], right["caption"] = "Table 2. Revenue", "Table 3. Revenue"
        self.assertIsNone(merge_boundary(left, right))

    def test_table_invalid_offsets_overlap_and_capacity_reject(self):
        for mutation in ("offset", "overlap", "missing_header"):
            right = table(2)
            cells = right["table_data"]["table_cells"]
            if mutation == "offset":
                cells[0]["end_col_offset_idx"] = 3
            elif mutation == "overlap":
                cells.append(copy.deepcopy(cells[0]))
            else:
                for cell in cells:
                    cell["column_header"] = False
            self.assertIsNone(merge_boundary(table(1), right))
        self.assertIsNone(merge_boundary(table(1), table(2), max_table_cells=7))

    def test_bounds_use_original_fragment_text_not_shrunk_display_text(self):
        left, right = prose("abc-", 1), prose("def", 2)
        self.assertIsNone(merge_boundary(left, right, max_chars=6))
        self.assertIsNotNone(merge_boundary(left, right, max_chars=7))

    def test_spanning_cells_cannot_bypass_merged_grid_capacity(self):
        def spanning_table(page, rows):
            block = table(page)
            block["table_data"]["num_rows"] = rows
            for cell in block["table_data"]["table_cells"]:
                if not cell["column_header"]:
                    cell["end_row_offset_idx"] = rows
            return block

        # Each source is a valid 1000 x 2 grid serialized as just four cells.
        # Joining would yield 1999 x 2 positions despite only six output cells.
        left, right = spanning_table(1, 1000), spanning_table(2, 1000)
        self.assertIsNone(merge_boundary(left, right))
        self.assertFalse(can_defer(left))
        # Exactly 2000 grid positions are allowed, but cannot be carried further.
        merged = merge_boundary(spanning_table(1, 501), spanning_table(2, 500))
        self.assertIsNotNone(merged)
        self.assertEqual(merged["table_data"]["num_rows"] * merged["table_data"]["num_cols"], 2000)
        self.assertEqual(len(merged["table_data"]["table_cells"]), 6)
        self.assertFalse(can_defer(merged))
        self.assertIsNone(merge_boundary(spanning_table(1, 501), spanning_table(2, 500),
                                         max_table_cells=1999))

    def test_fragments_are_detached_and_keep_original_whitespace(self):
        original = prose(" raw text \n", 1)
        fragments = source_fragments(original)
        self.assertEqual(fragments[0]["text"], " raw text \n")
        fragments[0]["pages"].append(10)
        self.assertEqual(original["pages"], [1])


if __name__ == "__main__":
    unittest.main()
