"""Writes the two VBA files (InvoiceEntry.bas, frmInvoice.frm) that go with invoice_entry_template.xlsx.
VBA files must be plain ANSI text with Windows (CRLF) line endings, so they are generated here."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

BAS = r'''Attribute VB_Name = "InvoiceEntry"
Option Explicit

' Invoice entry helpers -- pairs with the frmInvoice pop-up form.
' One tab = one batch. The tab name starts with the supplier's name; a second batch for the same
' supplier becomes "Supplier (2)", "Supplier (3)" ... The AP system matches tabs to suppliers by name.

Public Sub Auto_Open()
    Dim ws As Worksheet, b As Object
    Application.OnKey "^+i", "ShowInvoiceForm"
    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets("_Start")
    If Not ws Is Nothing Then
        ws.Buttons.Delete
        Set b = ws.Buttons.Add(ws.Range("B14").Left, ws.Range("B14").Top, 200, 34)
        b.Caption = "Add invoice  (Ctrl+Shift+I)"
        b.OnAction = "ShowInvoiceForm"
    End If
End Sub

Public Sub ShowInvoiceForm()
    frmInvoice.Show vbModeless
End Sub

Public Function BaseTabName(ByVal supplier As String) As String
    Dim s As String, ch As Variant
    s = supplier
    For Each ch In Array("\", "/", "?", "*", "[", "]", ":", "#")
        s = Replace(s, ch, " ")
    Next ch
    s = Application.WorksheetFunction.Trim(s)
    If Len(s) > 26 Then s = Trim(Left(s, 26))
    BaseTabName = s
End Function

Public Function IsBatchOf(ByVal sheetName As String, ByVal base As String) As Boolean
    IsBatchOf = (sheetName = base) Or (sheetName Like base & " (#)") Or (sheetName Like base & " (##)")
End Function

Public Function SheetExists(ByVal sheetName As String) As Boolean
    Dim ws As Worksheet
    For Each ws In ThisWorkbook.Worksheets
        If StrComp(ws.Name, sheetName, vbTextCompare) = 0 Then SheetExists = True: Exit Function
    Next ws
End Function

Public Function NewBatchSheet(ByVal base As String) As Worksheet
    Dim n As Long, nm As String, ws As Worksheet
    nm = base
    n = 1
    Do While SheetExists(nm)
        n = n + 1
        nm = base & " (" & n & ")"
    Loop
    Set ws = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets(ThisWorkbook.Worksheets.Count))
    ws.Name = nm
    ws.Range("A1").Value = "Sales Invoice No"
    ws.Range("B1").Value = "Date"
    ws.Range("C1").Value = "Amount"
    ws.Range("D1").Value = "Less %"
    ws.Range("E1").Value = "DR No"
    With ws.Range("A1:E1")
        .Font.Bold = True
        .Font.Color = RGB(255, 255, 255)
        .Interior.Color = RGB(31, 58, 95)
    End With
    ws.Columns("A").ColumnWidth = 22
    ws.Columns("B").ColumnWidth = 14
    ws.Columns("C").ColumnWidth = 16
    ws.Columns("D").ColumnWidth = 10
    ws.Columns("E").ColumnWidth = 14
    ws.Columns("E").NumberFormat = "@"
    ws.Columns("B").NumberFormat = "yyyy-mm-dd"
    ws.Columns("C").NumberFormat = "#,##0.00"
    ws.Range("G1").Value = "Invoices"
    ws.Range("H1").Formula = "=COUNTA(A2:A5000)"
    ws.Range("G2").Value = "Total"
    ws.Range("H2").Formula = "=SUM(C2:C5000)"
    ws.Range("H2").NumberFormat = "#,##0.00"
    ws.Range("G1:G2").Font.Bold = True
    Set NewBatchSheet = ws
End Function

' Name of the first tab of this supplier that already holds this sales invoice no., or "" if none.
Public Function FindDuplicate(ByVal base As String, ByVal docRef As String) As String
    Dim ws As Worksheet, r As Long, last As Long
    For Each ws In ThisWorkbook.Worksheets
        If IsBatchOf(ws.Name, base) Then
            last = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
            For r = 2 To last
                If StrComp(Trim(CStr(ws.Cells(r, 1).Value)), docRef, vbTextCompare) = 0 Then
                    FindDuplicate = ws.Name
                    Exit Function
                End If
            Next r
        End If
    Next ws
End Function
'''

FRM = r'''VERSION 5.00
Begin {C62A69F0-16DC-11CE-9E98-00AA00574A4F} frmInvoice
   Caption         =   "Add invoice"
   ClientHeight    =   4000
   ClientLeft      =   45
   ClientTop       =   375
   ClientWidth     =   5000
   StartUpPosition =   1  'CenterOwner
End
Attribute VB_Name = "frmInvoice"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Option Explicit

' The controls are created in code (UserForm_Initialize), so this form needs no designer work.

Private WithEvents cboSupplier As MSForms.ComboBox
Private WithEvents cboBatch As MSForms.ComboBox
Private WithEvents btnAdd As MSForms.CommandButton
Private WithEvents btnClose As MSForms.CommandButton
Private txtDoc As MSForms.TextBox
Private txtDr As MSForms.TextBox
Private WithEvents cboYear As MSForms.ComboBox
Private WithEvents cboMonth As MSForms.ComboBox
Private cboDay As MSForms.ComboBox
Private building As Boolean
Private txtAmount As MSForms.TextBox
Private txtLess As MSForms.TextBox
Private lblInfo As MSForms.Label
Private ctlCount As Long

Private Function AddCtl(ByVal progId As String, ByVal nm As String, ByVal x As Single, ByVal y As Single, ByVal w As Single, ByVal h As Single) As Object
    Dim c As Object
    Set c = Me.Controls.Add(progId, nm, True)
    c.Left = x
    c.Top = y
    c.Width = w
    c.Height = h
    Set AddCtl = c
End Function

Private Sub AddLbl(ByVal txt As String, ByVal y As Single)
    Dim l As Object
    ctlCount = ctlCount + 1
    Set l = AddCtl("Forms.Label.1", "lblAuto" & ctlCount, 12, y + 4, 90, 16)
    l.Caption = txt
End Sub

Private Sub UserForm_Initialize()
    Dim ws As Worksheet, r As Long, lastRow As Long
    Me.Caption = "Add invoice"
    Me.Width = 340
    Me.Height = 330

    AddLbl "Supplier", 12
    Set cboSupplier = AddCtl("Forms.ComboBox.1", "cboSupplier", 108, 12, 214, 20)
    cboSupplier.Style = 2
    AddLbl "Batch (tab)", 42
    Set cboBatch = AddCtl("Forms.ComboBox.1", "cboBatch", 108, 42, 214, 20)
    cboBatch.Style = 2
    AddLbl "Sales invoice (SI)", 72
    Set txtDoc = AddCtl("Forms.TextBox.1", "txtDoc", 108, 72, 214, 20)
    AddLbl "DR no. (if any)", 102
    Set txtDr = AddCtl("Forms.TextBox.1", "txtDr", 108, 102, 214, 20)
    AddLbl "Invoice date", 132
    Set cboMonth = AddCtl("Forms.ComboBox.1", "cboMonth", 108, 132, 96, 20)
    cboMonth.Style = 2
    Set cboDay = AddCtl("Forms.ComboBox.1", "cboDay", 208, 132, 52, 20)
    cboDay.Style = 2
    Set cboYear = AddCtl("Forms.ComboBox.1", "cboYear", 264, 132, 58, 20)
    cboYear.Style = 2
    AddLbl "Amount", 162
    Set txtAmount = AddCtl("Forms.TextBox.1", "txtAmount", 108, 162, 110, 20)
    AddLbl "Less %", 192
    Set txtLess = AddCtl("Forms.TextBox.1", "txtLess", 108, 192, 60, 20)
    Set lblInfo = AddCtl("Forms.Label.1", "lblInfo", 12, 226, 310, 32)
    lblInfo.Caption = "Pick a supplier to start."
    Set btnAdd = AddCtl("Forms.CommandButton.1", "btnAdd", 108, 266, 100, 28)
    btnAdd.Caption = "Add invoice"
    btnAdd.Default = True
    Set btnClose = AddCtl("Forms.CommandButton.1", "btnClose", 222, 266, 100, 28)
    btnClose.Caption = "Close"

    building = True
    FillYears
    SelectByNumber cboYear, Year(MaxDate())
    FillMonths
    FillDays
    building = False
    txtLess.Value = "0"

    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets("_Suppliers")
    On Error GoTo 0
    If Not ws Is Nothing Then
        lastRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
        For r = 2 To lastRow
            If Len(Trim(CStr(ws.Cells(r, 1).Value))) > 0 Then cboSupplier.AddItem CStr(ws.Cells(r, 1).Value)
        Next r
    End If
End Sub

' ---- Invoice date: three dropdowns that only offer real dates from the last 12 months up to today ----

Private Function MinDate() As Date
    MinDate = DateAdd("m", -12, Date)
End Function

Private Function MaxDate() As Date
    MaxDate = Date
End Function

Private Function SelNum(cbo As MSForms.ComboBox, ByVal dflt As Long) As Long
    If cbo.ListIndex < 0 Then
        SelNum = dflt
    Else
        SelNum = CLng(Val(cbo.List(cbo.ListIndex)))
    End If
End Function

Private Sub SelectByNumber(cbo As MSForms.ComboBox, ByVal n As Long)
    Dim i As Long
    For i = 0 To cbo.ListCount - 1
        If CLng(Val(cbo.List(i))) = n Then
            cbo.ListIndex = i
            Exit Sub
        End If
    Next i
End Sub

Private Sub FillYears()
    Dim y As Long
    cboYear.Clear
    For y = Year(MinDate()) To Year(MaxDate())
        cboYear.AddItem CStr(y)
    Next y
End Sub

Private Sub FillMonths()
    Dim y As Long, m As Long, m1 As Long, m2 As Long, keep As Long
    y = SelNum(cboYear, Year(MaxDate()))
    keep = SelNum(cboMonth, Month(MaxDate()))
    m1 = 1
    m2 = 12
    If y = Year(MinDate()) Then m1 = Month(MinDate())
    If y = Year(MaxDate()) Then m2 = Month(MaxDate())
    cboMonth.Clear
    For m = m1 To m2
        cboMonth.AddItem Format(m, "00") & " - " & Format(DateSerial(2000, m, 1), "mmm")
    Next m
    If keep < m1 Then keep = m1
    If keep > m2 Then keep = m2
    SelectByNumber cboMonth, keep
End Sub

Private Sub FillDays()
    Dim y As Long, m As Long, d As Long, d1 As Long, d2 As Long, keep As Long
    y = SelNum(cboYear, Year(MaxDate()))
    m = SelNum(cboMonth, Month(MaxDate()))
    keep = SelNum(cboDay, Day(MaxDate()))
    d1 = 1
    d2 = Day(DateSerial(y, m + 1, 0))
    If y = Year(MinDate()) And m = Month(MinDate()) Then d1 = Day(MinDate())
    If y = Year(MaxDate()) And m = Month(MaxDate()) Then d2 = Day(MaxDate())
    cboDay.Clear
    For d = d1 To d2
        cboDay.AddItem CStr(d)
    Next d
    If keep < d1 Then keep = d1
    If keep > d2 Then keep = d2
    SelectByNumber cboDay, keep
End Sub

Private Sub cboYear_Change()
    If building Then Exit Sub
    building = True
    FillMonths
    FillDays
    building = False
End Sub

Private Sub cboMonth_Change()
    If building Then Exit Sub
    building = True
    FillDays
    building = False
End Sub

Private Sub cboSupplier_Change()
    FillBatches
    RefreshInfo
End Sub

Private Sub cboBatch_Change()
    RefreshInfo
End Sub

Private Sub FillBatches()
    Dim ws As Worksheet, base As String
    cboBatch.Clear
    cboBatch.AddItem "(new batch)"
    If cboSupplier.ListIndex < 0 Then Exit Sub
    base = BaseTabName(cboSupplier.Value)
    For Each ws In ThisWorkbook.Worksheets
        If IsBatchOf(ws.Name, base) Then cboBatch.AddItem ws.Name
    Next ws
    cboBatch.ListIndex = cboBatch.ListCount - 1
End Sub

Private Sub RefreshInfo()
    Dim ws As Worksheet
    If cboBatch.ListIndex < 0 Then Exit Sub
    If cboBatch.Value = "(new batch)" Then
        lblInfo.Caption = "A new tab will be created for this batch."
    ElseIf SheetExists(cboBatch.Value) Then
        Set ws = ThisWorkbook.Worksheets(cboBatch.Value)
        lblInfo.Caption = ws.Name & ": " & ws.Range("H1").Value & " invoice(s), total " & Format(ws.Range("H2").Value, "#,##0.00")
    End If
End Sub

Private Sub btnClose_Click()
    Unload Me
End Sub

Private Sub btnAdd_Click()
    Dim base As String, docRef As String, drNo As String, amtText As String, lessText As String
    Dim amt As Double, less As Double, ws As Worksheet, r As Long, dupTab As String, invDate As Date

    If cboSupplier.ListIndex < 0 Then MsgBox "Pick a supplier first.", vbExclamation: Exit Sub
    docRef = Trim(txtDoc.Value)
    If Len(docRef) = 0 Then MsgBox "Type the sales invoice no. (SI). A DR number goes in the DR box.", vbExclamation: txtDoc.SetFocus: Exit Sub
    drNo = Trim(txtDr.Value)
    If cboYear.ListIndex < 0 Or cboMonth.ListIndex < 0 Or cboDay.ListIndex < 0 Then MsgBox "Pick the invoice date (month, day and year).", vbExclamation: Exit Sub
    invDate = DateSerial(SelNum(cboYear, 0), SelNum(cboMonth, 0), SelNum(cboDay, 0))
    If invDate > Date Then MsgBox "That invoice date is in the future (" & Format(invDate, "yyyy-mm-dd") & "). Please check it.", vbExclamation: Exit Sub
    If invDate < DateAdd("m", -12, Date) Then MsgBox "That invoice date (" & Format(invDate, "yyyy-mm-dd") & ") is more than 12 months ago. Please check the year and month.", vbExclamation: Exit Sub
    amtText = Replace(Trim(txtAmount.Value), ",", "")
    If Not IsNumeric(amtText) Then MsgBox "The amount must be a number.", vbExclamation: txtAmount.SetFocus: Exit Sub
    amt = CDbl(amtText)
    If amt <= 0 Then MsgBox "The amount must be more than zero.", vbExclamation: txtAmount.SetFocus: Exit Sub
    lessText = Replace(Replace(Trim(txtLess.Value), "%", ""), ",", "")
    If Len(lessText) = 0 Then lessText = "0"
    If Not IsNumeric(lessText) Then MsgBox "Less % must be a number (or 0).", vbExclamation: txtLess.SetFocus: Exit Sub
    less = CDbl(lessText)
    If less < 0 Or less > 100 Then MsgBox "Less % must be between 0 and 100.", vbExclamation: txtLess.SetFocus: Exit Sub

    base = BaseTabName(cboSupplier.Value)
    dupTab = FindDuplicate(base, docRef)
    If Len(dupTab) > 0 Then
        If MsgBox("Sales invoice " & docRef & " is already on tab """ & dupTab & """ for this supplier." & vbCrLf & vbCrLf & _
                  "Add it anyway?", vbYesNo + vbExclamation, "Possible double entry") <> vbYes Then Exit Sub
    End If

    If cboBatch.Value = "(new batch)" Then
        Set ws = NewBatchSheet(base)
        FillBatches
        cboBatch.Value = ws.Name
    Else
        Set ws = ThisWorkbook.Worksheets(cboBatch.Value)
    End If

    r = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row + 1
    If r < 2 Then r = 2
    ws.Cells(r, 1).NumberFormat = "@"
    ws.Cells(r, 1).Value = docRef
    ws.Cells(r, 2).Value = invDate
    ws.Cells(r, 3).Value = amt
    ws.Cells(r, 4).Value = less
    ws.Cells(r, 5).NumberFormat = "@"
    ws.Cells(r, 5).Value = drNo

    txtDoc.Value = ""
    txtDr.Value = ""
    txtAmount.Value = ""
    RefreshInfo
    txtDoc.SetFocus
End Sub
'''

# The .frm import can fail on some Excel installs ("Unspecified error"), so the same code is also written
# as plain text to paste into a UserForm you insert yourself (everything after the Attribute lines).
FORM_CODE = FRM[FRM.index("Option Explicit"):]

for name, text in (("InvoiceEntry.bas", BAS), ("frmInvoice.frm", FRM), ("frmInvoice_code.txt", FORM_CODE)):
    with open(os.path.join(HERE, name), "w", encoding="cp1252", newline="") as f:
        f.write(text.replace("\r\n", "\n").replace("\n", "\r\n"))
print("written")
