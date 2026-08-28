from flask import Flask, render_template, redirect, url_for, request, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from openpyxl import Workbook
import io
import json
from datetime import datetime
import os
import csv
import openpyxl

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui'

# --- Funções de Carregamento e Salvamento de Dados ---
def carregar_dados(caminho_arquivo, default_data):
    os.makedirs(os.path.dirname(caminho_arquivo), exist_ok=True)
    if not os.path.exists(caminho_arquivo) or os.path.getsize(caminho_arquivo) == 0:
        with open(caminho_arquivo, 'w', encoding='utf-8') as f:
            json.dump(default_data, f)
    with open(caminho_arquivo, 'r', encoding='utf-8') as f:
        return json.load(f)

def salvar_dados(caminho_arquivo, data):
    with open(caminho_arquivo, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def carregar_usuarios():
    caminho = os.path.join('data', 'usuarios.json')
    if not os.path.exists(caminho):
        return []
    with open(caminho, 'r', encoding='utf-8') as f:
        return json.load(f)

def salvar_usuarios(usuarios):
    salvar_dados('data/usuarios.json', usuarios)

def carregar_materiais():
    return carregar_dados('data/materiais.json', [])

def salvar_materiais(materiais):
    salvar_dados('data/materiais.json', materiais)

def carregar_requisicoes():
    return carregar_dados('data/requisicoes.json', [])

def salvar_requisicoes(requisicoes):
    salvar_dados('data/requisicoes.json', requisicoes)

def carregar_departamentos():
    return carregar_dados('data/departamentos.json', ["Logística", "Recursos Humanos", "Financeiro", "TI", "Produção", "MAF Betim", "MAF Porto Real"])

def salvar_departamentos(departamentos):
    salvar_dados('data/departamentos.json', departamentos)

# --- Decoradores de Autenticação e Autorização ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            flash('Por favor, faça login para acessar esta página.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session or session['user'].get('permissao') != 'administrador':
            flash('Você não tem permissão para acessar esta página.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Rotas do Aplicativo ---
@app.route('/')
def home():
    if 'user' in session:
        if session['user'].get('permissao') == 'administrador':
            return redirect(url_for('pendentes'))
        else:
            return redirect(url_for('fazer_requisicao'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        registro = str(request.form.get('registro', '')).strip()
        senha = request.form.get('senha', '')

        usuarios = carregar_usuarios()
        usuario = next((u for u in usuarios if str(u.get('registro', '')).strip() == registro), None)

        if usuario:
            senha_salva = usuario.get('senha', '')
            
            senha_valida = False
            if senha_salva and (senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:')):
                senha_valida = check_password_hash(senha_salva, senha)
            else:
                senha_valida = (senha_salva == senha)

            if senha_valida:
                session['user'] = usuario
                
                # --- REDIRECIONAMENTO DE ACORDO COM A PERMISSÃO ---
                if usuario.get('permissao') == 'administrador':
                    return redirect(url_for('pendentes'))
                else:
                    return redirect(url_for('fazer_requisicao'))

        error = 'Número de registro ou senha incorretos.'

    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/pendentes')
@login_required
@admin_required
def pendentes():
    requisicoes = carregar_requisicoes()
    pendentes_list = [r for r in requisicoes if r.get('status') == 'pendente']
    return render_template('pendentes.html', requisicoes=pendentes_list)

@app.route('/separados')
@login_required
@admin_required
def separados():
    requisicoes = carregar_requisicoes()
    separados_list = [r for r in requisicoes if r.get('status') == 'separado']
    return render_template('separados.html', requisicoes=separados_list)

@app.route('/enviar_requisicao', methods=['POST'])
@login_required
def enviar_requisicao():
    materiais_disponiveis = carregar_materiais()
    itens_requisicao = []

    categoria_solicitacao = request.form.get('categoria_solicitacao', 'escritorio')

    for key, value in request.form.items():
        if key.startswith('materiais[') and key.endswith('][codigo]'):
            index = key.split('[')[1].split(']')[0]
            codigo_material = value
            quantidade = request.form.get(f'materiais[{index}][quantidade]')

            if not codigo_material or not quantidade:
                continue

            quantidade = int(quantidade)
            
            material_encontrado = next((m for m in materiais_disponiveis if m['codigo'] == codigo_material), None)
            
            if not material_encontrado:
                flash(f'Material com código {codigo_material} não encontrado.', 'danger')
                return redirect(url_for('fazer_requisicao'))

            if quantidade > material_encontrado['quantidade_maxima']:
                flash(f"A quantidade de '{material_encontrado['descricao']}' excede o limite de {material_encontrado['quantidade_maxima']} por requisição.", 'warning')
                return redirect(url_for('fazer_requisicao'))
            
            itens_requisicao.append({
                'codigo': codigo_material,
                'nome': material_encontrado['descricao'],
                'quantidade': quantidade
            })

    if not itens_requisicao:
        flash('Nenhum item foi adicionado à requisição.', 'warning')
        return redirect(url_for('fazer_requisicao'))

    requisicoes = carregar_requisicoes()
    usuario_logado = session['user']
    
    nova_requisicao = {
        'id': len(requisicoes) + 1,
        'nome': usuario_logado.get('nome'),
        'registro': usuario_logado.get('registro'),
        'departamento': usuario_logado.get('departamento'),
        'categoria': categoria_solicitacao,
        'data': datetime.now().strftime('%d/%m/%Y %H:%M'),
        'status': 'pendente',
        'itens': itens_requisicao
    }

    requisicoes.append(nova_requisicao)
    salvar_requisicoes(requisicoes)
    
    flash('Sua requisição foi enviada com sucesso e está pendente de aprovação!', 'success')
    return redirect(url_for('fazer_requisicao', categoria=categoria_solicitacao))

@app.route('/concluir_requisicao', methods=['POST'])
@login_required
def concluir_requisicao():
    requisicao_id = int(request.form.get('requisicao_id'))
    data_retirada = request.form.get('data_retirada')
    
    requisicoes = carregar_requisicoes()
    requisicao = next((r for r in requisicoes if r['id'] == requisicao_id), None)
    
    if requisicao:
        requisicao['status'] = 'separado'
        requisicao['data_retirada'] = data_retirada
        requisicao['separado_por'] = session['user'].get('nome', '')
        salvar_requisicoes(requisicoes)
        flash('Requisição marcada como separada com sucesso!', 'success')
    else:
        flash('Requisição não encontrada.', 'danger')
        
    return redirect(url_for('pendentes'))

@app.route('/finalizar_requisicao', methods=['POST'])
@login_required
def finalizar_requisicao():
    requisicao_id = str(request.form.get('requisicao_id'))
    registro_solicitante = str(request.form.get('registro_solicitante', '')).strip()
    senha_solicitante = request.form.get('senha_solicitante')

    requisicoes = carregar_requisicoes()
    requisicao = next((r for r in requisicoes if str(r['id']) == requisicao_id), None)

    if not requisicao:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('separados'))

    departamento_req = str(requisicao.get('departamento', '')).lower()
    eh_isento = ('porto real' in departamento_req) or ('betim' in departamento_req)

    if not eh_isento:
        usuarios = carregar_usuarios()
        solicitante = next((u for u in usuarios if str(u.get('registro', '')).strip() == registro_solicitante), None)

        if not solicitante:
            flash('Registro do solicitante não encontrado.', 'danger')
            return redirect(url_for('separados'))

        senha_salva = solicitante.get('senha', '')
        senha_valida = False
        if senha_salva and (senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:')):
            senha_valida = check_password_hash(senha_salva, senha_solicitante)
        else:
            senha_valida = (senha_salva == senha_solicitante)

        if not senha_valida:
            flash('Senha do solicitante incorreta. A requisição não foi finalizada.', 'danger')
            return redirect(url_for('separados'))

    requisicao['status'] = 'concluida'
    requisicao['data_conclusao'] = datetime.now().strftime('%d/%m/%Y %H:%M')
    salvar_requisicoes(requisicoes)

    flash('Requisição finalizada com sucesso!', 'success')
    return redirect(url_for('separados'))

@app.route('/excluir_usuario', methods=['POST'])
@login_required
@admin_required
def excluir_usuario():
    registro_usuario_excluir = request.form.get('registro_usuario')
    registro_usuario_logado = session['user'].get('registro')

    if registro_usuario_excluir == registro_usuario_logado:
        flash('Você não pode excluir a si mesmo.', 'warning')
        return redirect(url_for('gerenciar_usuarios'))

    usuarios = carregar_usuarios()
    usuarios_atualizados = [u for u in usuarios if u['registro'] != registro_usuario_excluir]
    salvar_usuarios(usuarios_atualizados)
    
    flash(f'Usuário com registro {registro_usuario_excluir} excluído com sucesso.', 'success')
    return redirect(url_for('gerenciar_usuarios'))

@app.route('/fazer_requisicao')
@login_required
def fazer_requisicao():
    materiais = carregar_materiais()
    departamentos = carregar_departamentos()
    
    usuario_logado = session['user']
    departamento_usuario = usuario_logado.get('departamento', '')
    permissao_usuario = usuario_logado.get('permissao', '')
    
    deps_permitidos_epi = ["MAF Betim", "MAF Porto Real"]
    
    pode_acessar_epi = (
        permissao_usuario == 'administrador' or 
        departamento_usuario in deps_permitidos_epi
    )
    
    categoria_atual = request.args.get('categoria', 'escritorio')
    
    if categoria_atual == 'epi' and not pode_acessar_epi:
        flash('Seu departamento não possui permissão para solicitar EPIs.', 'warning')
        return redirect(url_for('fazer_requisicao', categoria='escritorio'))
    
    materiais_filtrados = [
        m for m in materiais 
        if m.get('categoria', 'escritorio') == categoria_atual
    ]
    
    return render_template(
        'fazer_requisicao.html', 
        materiais=materiais_filtrados, 
        departamentos=departamentos,
        categoria_atual=categoria_atual,
        pode_acessar_epi=pode_acessar_epi
    )

@app.route('/historico')
@login_required
def historico():
    requisicoes = carregar_requisicoes()
    
    if session['user'].get('permissao') == 'administrador':
        requisicoes_a_exibir = requisicoes
    else:
        registro_usuario = str(session['user'].get('registro', '')).strip()
        requisicoes_a_exibir = [
            req for req in requisicoes
            if str(req.get('registro', '')).strip() == registro_usuario
        ]

    # Inverte a ordem da lista para exibir do mais novo para o mais antigo
    requisicoes_ordenadas = requisicoes_a_exibir[::-1]

    return render_template('historico.html', requisicoes=requisicoes_ordenadas)

@app.route('/excluir_requisicao/<int:requisicao_id>', methods=['POST'])
@login_required
def excluir_requisicao(requisicao_id):
    requisicoes = carregar_requisicoes()
    requisicao_para_excluir = next((req for req in requisicoes if req['id'] == requisicao_id), None)

    if requisicao_para_excluir:
        registro_usuario = str(session['user'].get('registro', '')).strip()
        registro_pedido = str(requisicao_para_excluir.get('registro', '')).strip()

        if registro_pedido == registro_usuario and requisicao_para_excluir.get('status') == 'pendente':
            requisicoes.remove(requisicao_para_excluir)
            salvar_requisicoes(requisicoes)
            flash('Requisição excluída com sucesso!', 'success')
        else:
            flash('Você não pode excluir uma requisição que já foi separada ou concluída.', 'danger')
    else:
        flash('Requisição não encontrada.', 'danger')

    return redirect(url_for('historico'))

@app.route('/gerenciar_materiais_escritorio', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_materiais_escritorio():
    if request.method == 'POST':
        codigo = request.form.get('codigo')
        descricao = request.form.get('descricao')
        quantidade_maxima = request.form.get('quantidade_maxima')

        materiais = carregar_materiais()
        novo_material = {
            "codigo": codigo,
            "descricao": descricao,
            "quantidade_maxima": int(quantidade_maxima),
            "categoria": "escritorio"
        }
        materiais.append(novo_material)
        salvar_materiais(materiais)
        flash('Material de escritório adicionado com sucesso!', 'success')
        return redirect(url_for('gerenciar_materiais_escritorio'))
        
    materiais = carregar_materiais()
    materiais_escritorio = [m for m in materiais if m.get('categoria', 'escritorio') == 'escritorio']
    return render_template('gerenciar_materiais_escritorio.html', materiais=materiais_escritorio)

@app.route('/gerenciar_materiais_epi', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_materiais_epi():
    if request.method == 'POST':
        codigo = request.form.get('codigo')
        descricao = request.form.get('descricao')
        quantidade_maxima = request.form.get('quantidade_maxima')

        materiais = carregar_materiais()
        novo_material = {
            "codigo": codigo,
            "descricao": descricao,
            "quantidade_maxima": int(quantidade_maxima),
            "categoria": "epi"
        }
        materiais.append(novo_material)
        salvar_materiais(materiais)
        flash('EPI adicionado com sucesso!', 'success')
        return redirect(url_for('gerenciar_materiais_epi'))
        
    materiais = carregar_materiais()
    materiais_epi = [m for m in materiais if m.get('categoria') == 'epi']
    return render_template('gerenciar_materiais_epi.html', materiais=materiais_epi)

@app.route('/excluir_material', methods=['POST'])
@login_required
@admin_required
def excluir_material():
    codigo_material_excluir = request.form.get('codigo_material')
    materiais = carregar_materiais()
    
    materiais_atualizados = [m for m in materiais if m['codigo'] != codigo_material_excluir]
    
    salvar_materiais(materiais_atualizados)
    flash('Material excluído com sucesso!', 'success')
    return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

@app.route('/editar_material/<codigo_material>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_material(codigo_material):
    materiais = carregar_materiais()
    material = next((m for m in materiais if m['codigo'] == codigo_material), None)

    if not material:
        flash('Material não encontrado.', 'danger')
        return redirect(url_for('gerenciar_materiais_escritorio'))

    if request.method == 'POST':
        nova_descricao = request.form.get('descricao')
        nova_quantidade_maxima = request.form.get('quantidade_maxima')

        material['descricao'] = nova_descricao
        material['quantidade_maxima'] = int(nova_quantidade_maxima)

        salvar_materiais(materiais)
        flash('Material atualizado com sucesso!', 'success')

        # Redireciona de volta para a categoria correspondente
        if material.get('categoria') == 'epi':
            return redirect(url_for('gerenciar_materiais_epi'))
        return redirect(url_for('gerenciar_materiais_escritorio'))

    return render_template('editar_material.html', material=material)

@app.route('/cadastro')
@login_required
@admin_required
def cadastro():
    departamentos = carregar_departamentos()
    return render_template('cadastro.html', departamentos=departamentos)

@app.route('/cadastrar_usuario', methods=['POST'])
@login_required
def cadastrar_usuario():
    if session.get('user', {}).get('permissao') != 'administrador':
        flash('Acesso negado.', 'danger')
        return redirect(url_for('fazer_requisicao'))

    nome = request.form.get('nome')
    registro = str(request.form.get('registro')).strip()
    departamento = request.form.get('departamento')
    permissao = request.form.get('permissao')
    senha_bruta = request.form.get('senha')

    usuarios = carregar_usuarios()

    if any(str(u.get('registro')).strip() == registro for u in usuarios):
        flash('Já existe um usuário cadastrado com este registro/matrícula.', 'danger')
        return redirect(url_for('gerenciar_usuarios'))

    novo_usuario = {
        'registro': registro,
        'nome': nome,
        'departamento': departamento,
        'permissao': permissao,
        'senha': generate_password_hash(senha_bruta) if senha_bruta else None
    }

    usuarios.append(novo_usuario)
    salvar_usuarios(usuarios)

    flash('Usuário cadastrado com sucesso e senha criptografada!', 'success')
    return redirect(url_for('gerenciar_usuarios'))

@app.route('/gerenciar_departamentos', methods=['GET', 'POST'])
@login_required
@admin_required
def gerenciar_departamentos():
    if request.method == 'POST':
        nome_departamento = request.form.get('nome', '').strip()
        departamentos = carregar_departamentos()

        if nome_departamento and nome_departamento not in departamentos:
            departamentos.append(nome_departamento)
            salvar_departamentos(departamentos)
            flash(f'Departamento "{nome_departamento}" adicionado com sucesso!', 'success')
        else:
            flash('Departamento já existe ou nome inválido.', 'warning')
            
        return redirect(url_for('gerenciar_departamentos'))

    departamentos = carregar_departamentos()
    return render_template('gerenciar_departamentos.html', departamentos=departamentos)

@app.route('/excluir_departamento', methods=['POST'])
@login_required
@admin_required
def excluir_departamento():
    nome_excluir = request.form.get('nome')
    departamentos = carregar_departamentos()

    if nome_excluir in departamentos:
        departamentos.remove(nome_excluir)
        salvar_departamentos(departamentos)
        flash(f'Departamento "{nome_excluir}" removido.', 'success')

    return redirect(url_for('gerenciar_departamentos'))

@app.route('/gerenciar_usuarios')
@login_required
@admin_required
def gerenciar_usuarios():
    usuarios = carregar_usuarios()
    return render_template('gerenciar_usuarios.html', usuarios=usuarios)

@app.route('/trocar_senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual')
        nova_senha = request.form.get('nova_senha')
        confirmar_nova_senha = request.form.get('confirmar_nova_senha')

        usuarios = carregar_usuarios()
        registro_usuario_logado = session['user'].get('registro')
        
        usuario_encontrado = next((u for u in usuarios if str(u.get('registro')).strip() == str(registro_usuario_logado).strip()), None)

        if usuario_encontrado:
            senha_salva = usuario_encontrado.get('senha', '')
            
            senha_valida = False
            if senha_salva and (senha_salva.startswith('scrypt:') or senha_salva.startswith('pbkdf2:')):
                senha_valida = check_password_hash(senha_salva, senha_atual)
            else:
                senha_valida = (senha_salva == senha_atual)

            if senha_valida:
                if nova_senha == confirmar_nova_senha:
                    if len(nova_senha) >= 8:
                        usuario_encontrado['senha'] = generate_password_hash(nova_senha)
                        salvar_usuarios(usuarios)
                        session['user']['senha'] = usuario_encontrado['senha']
                        flash('Sua senha foi alterada com sucesso!', 'success')
                        return redirect(url_for('historico'))
                    else:
                        flash('A nova senha deve ter no mínimo 8 caracteres.', 'danger')
                else:
                    flash('As novas senhas não coincidem.', 'danger')
            else:
                flash('A senha atual está incorreta.', 'danger')

    return render_template('trocar_senha.html')

@app.route('/exportar_historico_excel')
@login_required
@admin_required
def exportar_historico_excel():
    requisicoes = carregar_requisicoes()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Histórico de Requisições"

    headers = ["ID", "Data da Requisição", "Status", "Solicitante", "Departamento", "Tipo", "Descrição do Item", "Quantidade", "Separado Por", "Data de Retirada", "Data de Conclusão"]
    sheet.append(headers)

    for r in requisicoes:
        tipo_desc = "EPI" if r.get('categoria') == 'epi' else "Escritório"
        data_requisicao = r.get('data', '-')
        data_retirada = r.get('data_retirada', '-')
        data_conclusao = r.get('data_conclusao', '-')
        separado_por = r.get('separado_por', '-')
        
        for item in r.get('itens', []):
            row_data = [
                r.get('id'),
                data_requisicao,
                r.get('status'),
                r.get('nome'),
                r.get('departamento'),
                tipo_desc,
                item.get('nome'),
                item.get('quantidade'),
                separado_por,
                data_retirada,
                data_conclusao
            ]
            sheet.append(row_data)

    excel_file = io.BytesIO()
    workbook.save(excel_file)
    excel_file.seek(0)
    
    return send_file(
        excel_file,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='historico_de_requisicoes.xlsx'
    )

@app.route('/requisicao/<int:requisicao_id>')
@login_required
def ver_requisicao(requisicao_id):
    requisicoes = carregar_requisicoes()
    requisicao = next((r for r in requisicoes if r['id'] == requisicao_id), None)
    
    if not requisicao:
        flash('Requisição não encontrada.', 'danger')
        return redirect(url_for('historico'))
    
    usuario_logado = session['user']
    registro_logado = str(usuario_logado.get('registro', '')).strip().lower()
    nome_logado = str(usuario_logado.get('nome', '')).strip().lower()
    permissao_logada = usuario_logado.get('permissao', '')
    
    registro_requisicao = str(requisicao.get('registro', '')).strip().lower()
    nome_requisicao = str(requisicao.get('nome', '')).strip().lower()
    
    eh_dono = (registro_requisicao == registro_logado) or (nome_requisicao == nome_logado)
    
    if permissao_logada != 'administrador' and not eh_dono:
        flash('Você não tem permissão para visualizar esta requisição.', 'danger')
        return redirect(url_for('historico'))

    return render_template('ver_requisicao.html', requisicao=requisicao)

@app.route('/importar_materiais_csv', methods=['POST'])
@login_required
@admin_required
def importar_materiais_csv():
    categoria = request.form.get('categoria', 'escritorio')
    file = request.files.get('arquivo_csv')

    if not file or not file.filename:
        flash('Nenhum arquivo foi selecionado.', 'danger')
        return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

    filename = file.filename.lower()
    materiais = carregar_materiais()
    codigos_existentes = {m['codigo'] for m in materiais}
    novos_cadastrados = 0

    try:
        # --- 1. SE FOR ARQUIVO EXCEL (.xlsx) ---
        if filename.endswith('.xlsx'):
            workbook = openpyxl.load_workbook(file)
            sheet = workbook.active
            rows = list(sheet.iter_rows(values_only=True))

            if not rows:
                flash('A planilha enviada está vazia.', 'warning')
                return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

            # Pega os nomes das colunas da primeira linha
            header = [str(cell).strip().lower() if cell is not None else '' for cell in rows[0]]

            # Mapeia os índices das colunas 'codigo', 'descricao' e 'quantidade_maxima'
            idx_codigo = header.index('codigo') if 'codigo' in header else 0
            idx_desc = header.index('descricao') if 'descricao' in header else 1
            idx_qtd = header.index('quantidade_maxima') if 'quantidade_maxima' in header else 2

            for row in rows[1:]:
                if not row or len(row) <= idx_codigo:
                    continue
                codigo = str(row[idx_codigo] or '').strip()
                descricao = str(row[idx_desc] or '').strip() if len(row) > idx_desc else ''
                qtd_max = str(row[idx_qtd] or '').strip() if len(row) > idx_qtd else ''

                if codigo and descricao and qtd_max and codigo not in codigos_existentes:
                    materiais.append({
                        "codigo": codigo,
                        "descricao": descricao,
                        "quantidade_maxima": int(float(qtd_max)),
                        "categoria": categoria
                    })
                    codigos_existentes.add(codigo)
                    novos_cadastrados += 1

        # --- 2. SE FOR ARQUIVO CSV (.csv) ---
        elif filename.endswith('.csv'):
            content = file.stream.read().decode("utf-8-sig", errors="ignore")
            
            # Detecta se o separador é vírgula ou ponto e vírgula
            delimiter = ';' if ';' in content else ','
            stream = io.StringIO(content)
            reader = csv.DictReader(stream, delimiter=delimiter)

            for row in reader:
                # Normaliza as chaves do cabeçalho para minúsculas
                row_clean = {str(k).strip().lower(): str(v).strip() for k, v in row.items() if k}
                codigo = row_clean.get('codigo', '')
                descricao = row_clean.get('descricao', '')
                qtd_max = row_clean.get('quantidade_maxima', '')

                if codigo and descricao and qtd_max and codigo not in codigos_existentes:
                    materiais.append({
                        "codigo": codigo,
                        "descricao": descricao,
                        "quantidade_maxima": int(float(qtd_max)),
                        "categoria": categoria
                    })
                    codigos_existentes.add(codigo)
                    novos_cadastrados += 1

        else:
            flash('Formato inválido. Por favor, envie um arquivo .csv ou .xlsx (Excel).', 'danger')
            return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))

        salvar_materiais(materiais)
        flash(f'Sucesso! {novos_cadastrados} novos materiais foram importados.', 'success')

    except Exception as e:
        flash(f'Erro ao processar o arquivo: {str(e)}', 'danger')

    return redirect(request.referrer or url_for('gerenciar_materiais_escritorio'))
if __name__ == '__main__':
    app.run(debug=True)