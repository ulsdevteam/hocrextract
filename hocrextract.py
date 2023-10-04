import codecs
import logging
import os
from typing import List
import pdftotree
from argparse import ArgumentParser
from functools import cmp_to_key
from pathlib import Path
from pdfminer.layout import LAParams, LTPage, LTFigure, LTImage
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from pdftotree import TreeExtract
from pdftotree.utils.pdf.pdf_utils import CustomPDFPageAggregator
from pdftotree.utils.pdf.vector_utils import column_order
from xml.dom.minidom import Document

class CustomTreeExtractor(TreeExtract.TreeExtractor):
    """Extracts HOCR info to separate files and scales based on the size of the bg image"""

    def parse(self):
        layouts: List[LTPage] = []
        log = logging.getLogger(__name__)

        # Open a PDF file.
        with open(os.path.realpath(self.pdf_file), "rb") as fp:
            # Create a PDF parser object associated with the file object.
            parser = PDFParser(fp)
            # Create a PDF document object that stores the document structure.
            # Supply the password for initialization.
            document = PDFDocument(parser, password="")
            # Create a PDF resource manager object that stores shared resources.
            rsrcmgr = PDFResourceManager()
            # Set parameters for analysis.
            laparams = LAParams(char_margin=1.0, word_margin=0.1, detect_vertical=True)
            # Create a PDF page aggregator object.
            device = CustomPDFPageAggregator(rsrcmgr, laparams=laparams)
            # Create a PDF interpreter object.
            interpreter = PDFPageInterpreter(rsrcmgr, device)
            # Process each page contained in the document.
            for page_num, page in enumerate(PDFPage.create_pages(document)):
                try:
                    interpreter.process_page(page)
                except OverflowError as oe:
                    log.exception(
                        "{}, skipping page {} of {}".format(oe, page_num, self.pdf_file)
                    )
                    continue
                layout = device.get_result()
                layouts.append(layout)

        for page_num, layout in enumerate(layouts):
            page_num += 1  # indexes start at 1
            bg_figure = next(x for x in layout._objs if isinstance(x, LTFigure) and x.width == layout.width)
            bg_image = next(x for x in bg_figure._objs if isinstance(x, LTImage))
            scale_factor = bg_image.srcsize[0] / layout.width
            elems, font_stat = device.normalize_pdf(layout, scale_factor)
            self.elems[page_num] = elems
            self.font_stats[page_num] = font_stat

    def get_html_for_page(self, page_num: int):
        doc = Document()
        self.doc = doc
        html = doc.createElement("html")
        doc.appendChild(html)
        html.setAttribute("xmlns", "http://www.w3.org/1999/xhtml")
        head = doc.createElement("head")
        html.appendChild(head)
        # meta
        meta = doc.createElement("meta")
        head.appendChild(meta)
        meta.setAttribute("name", "ocr-system")
        meta.setAttribute("content", f"Extracted from PDF by hocrextract/pdftotree {pdftotree.__version__}")
        meta = doc.createElement("meta")
        head.appendChild(meta)
        meta.setAttribute("name", "ocr-capabilities")
        meta.setAttribute("content", "ocr_page ocr_table ocrx_block ocrx_word")
        # body
        body = doc.createElement("body")
        html.appendChild(body)
        boxes = []
        for clust in self.tree[page_num]:
            for (pnum, pwidth, pheight, top, left, bottom, right) in self.tree[
                page_num
            ][clust]:
                boxes += [
                    [clust.lower().replace(" ", "_"), top, left, bottom, right]
                ]
        page = doc.createElement("div")
        page.setAttribute("class", "ocr_page")
        page.setAttribute("id", "page_1")
        width = int(self.elems[page_num].layout.width)
        height = int(self.elems[page_num].layout.height)
        page.setAttribute(
            "title",
            f"bbox 0 0 {width} {height}; ppageno 0",
        )
        body.appendChild(page)
        boxes.sort(key=cmp_to_key(column_order))

        for box in boxes:
            if box[0] == "table":
                table = box[1:]  # bbox
                table_element = self.get_html_table(table, page_num)
                page.appendChild(table_element)
            elif box[0] == "figure":
                fig_element = doc.createElement("figure")
                page.appendChild(fig_element)
                top, left, bottom, right = [int(i) for i in box[1:]]
                fig_element.setAttribute(
                    "title", f"bbox {left} {top} {right} {bottom}"
                )
            else:
                element = self.get_html_others(box[0], box[1:], page_num)
                page.appendChild(element)
        return doc.toprettyxml()

if __name__ == "__main__":
    parser = ArgumentParser(
        description="""
        Extract HOCR data from pdf
        """,
    )
    parser.add_argument(
        "pdf_file",
        type=str,
        help="Path to input PDF"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="""
        Path to output folder.
        If not given, a folder will be created in the current working directory based on the input filename.
        """
    )
    parser.add_argument(
        "-p",
        "--page-offset",
        type=int,
        help="Offsets the page number in the output filename",
        default=0
    )
    args = parser.parse_args()
    output_folder = args.output or Path(args.pdf_file).stem
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    extractor = CustomTreeExtractor(args.pdf_file)
    extractor.parse()
    extractor.get_tree_structure(None, None)
    for page_num in extractor.get_elems().keys():
        page_html = extractor.get_html_for_page(page_num)
        output_path = Path(output_folder, "{}-{}_HOCR.shtml".format(
            Path(args.pdf_file).stem,
            str(page_num + args.page_offset).zfill(4)
        ))
        with codecs.open(output_path, encoding="utf-8", mode="w") as file:
            file.write(page_html)
